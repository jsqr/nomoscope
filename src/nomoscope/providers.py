"""The model providers nomoscope calls, and the one POST all their calls use.

A provider names the environment variable that holds its key. `post`
retries throttles and server errors, waiting the server's Retry-After or
backing off, and returns the JSON body of a 200.
"""

import logging
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

from nomoscope.errors import NomoscopeError

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, read=300.0)
RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# A provider that throttles a sustained rate answers 429 steadily during a
# long run; waiting out its window is cheaper than losing the run.
ATTEMPTS = 8
BACKOFF = 2.0
MAX_WAIT = 60.0


class ProviderError(NomoscopeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        # None when no HTTP reply was refused, e.g. a missing key.
        self.status = status


@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    key_env: str


FIREWORKS = Provider("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY")
OPENROUTER = Provider("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")
PROVIDERS = {p.name: p for p in (FIREWORKS, OPENROUTER)}


def api_key(provider: Provider) -> str:
    key = os.environ.get(provider.key_env)
    if not key:
        raise ProviderError(f"set {provider.key_env} for {provider.name}")
    return key


def make_client(provider: Provider) -> httpx.Client:
    return httpx.Client(
        base_url=provider.base_url,
        headers={"Authorization": f"Bearer {api_key(provider)}"},
        timeout=TIMEOUT,
    )


def retry_wait(
    response: httpx.Response, attempt: int, jitter: Callable[[], float] = random.random
) -> float:
    """Seconds to wait before attempt `attempt` + 1: the server's Retry-After,
    else exponential backoff; jittered, so throttled workers do not return together."""
    after = response.headers.get("retry-after", "")
    base = float(after) if after.replace(".", "", 1).isdigit() else BACKOFF**attempt
    return min(base, MAX_WAIT) * (0.5 + jitter())


def post(
    client: httpx.Client,
    path: str,
    payload: dict[str, Any],
    *,
    attempts: int = ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """POST `path`, retrying throttles and server errors. Returns the body."""
    response = client.post(path, json=payload)
    for attempt in range(1, attempts):
        if response.status_code not in RETRY_STATUS:
            break
        wait = retry_wait(response, attempt, jitter)
        log.debug("%s HTTP %d, retrying in %.0fs", payload["model"], response.status_code, wait)
        sleep(wait)
        response = client.post(path, json=payload)
    if response.status_code != 200:
        raise ProviderError(
            f"{payload['model']}: HTTP {response.status_code}: {response.text}",
            response.status_code,
        )
    return response.json()
