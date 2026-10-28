"""Calls to an embedding provider: vectors, and reranking scores.

Both go to `POST {base_url}/embeddings`. A vector request asks for the
model's width (`dimensions`); each vector is checked for that width and
normalized again, since the provider does not normalize. A rerank request
sends one prompt per document and asks for the logits of "no" and "yes",
softmaxed (`normalize`); the score is the probability of "yes".
"""

import logging
import math
import random
import time
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from nomoscope.embed.models import EmbeddingModel, EmbedError, Provider, Reranker, api_key
from nomoscope.segments import CHARS_PER_TOKEN

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, read=300.0)
RETRY_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
# A provider that throttles a sustained rate answers 429 steadily during a
# long run; waiting out its window is cheaper than losing the run.
ATTEMPTS = 8
BACKOFF = 2.0
MAX_WAIT = 60.0

BATCH_INPUTS = 64
BATCH_TOKENS = 16000

Embed = Callable[[Sequence[str]], list[list[float]]]


def estimated_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


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
    payload: dict[str, Any],
    *,
    attempts: int = ATTEMPTS,
    sleep: Callable[[float], None] = time.sleep,
    jitter: Callable[[], float] = random.random,
) -> dict[str, Any]:
    """POST /embeddings, retrying throttles and server errors. Returns the body."""
    response = client.post("/embeddings", json=payload)
    for attempt in range(1, attempts):
        if response.status_code not in RETRY_STATUS:
            break
        wait = retry_wait(response, attempt, jitter)
        log.debug("%s HTTP %d, retrying in %.0fs", payload["model"], response.status_code, wait)
        sleep(wait)
        response = client.post("/embeddings", json=payload)
    if response.status_code != 200:
        raise EmbedError(f"{payload['model']}: HTTP {response.status_code}: {response.text}")
    return response.json()


def embed(
    client: httpx.Client, model: EmbeddingModel, texts: Sequence[str], **retry
) -> list[list[float]]:
    """One request. Unit vectors of the model's width, in the order of `texts`."""
    if not texts:
        return []
    body = post(
        client, {"model": model.name, "input": list(texts), "dimensions": model.dim}, **retry
    )
    found = ordered(body, model.slug, len(texts))
    wrong = next((len(v) for v in found if len(v) != model.dim), None)
    if wrong is not None:
        raise EmbedError(f"{model.slug}: expected {model.dim} dimensions, got {wrong}")
    return [unit(v) for v in found]


def rerank(client: httpx.Client, model: Reranker, prompts: Sequence[str], **retry) -> list[float]:
    """One request. The probability of "yes" for each prompt, in order."""
    if not prompts:
        return []
    payload = {
        "model": model.name,
        "input": list(prompts),
        "return_logits": list(model.logits),
        "normalize": True,
    }
    found = ordered(post(client, payload, **retry), model.slug, len(prompts))
    if any(len(p) != 2 for p in found):
        raise EmbedError(f"{model.slug}: expected two probabilities per document")
    return [p[1] for p in found]


def ordered(body: dict[str, Any], slug: str, count: int) -> list[list[float]]:
    found = [d["embedding"] for d in sorted(body.get("data", []), key=lambda d: d["index"])]
    if len(found) != count:
        raise EmbedError(f"{slug}: asked for {count} results, got {len(found)}")
    return found


def unit(vector: Sequence[float]) -> list[float]:
    norm = math.hypot(*vector)
    if norm == 0:
        raise EmbedError("the provider returned a zero vector")
    return [x / norm for x in vector]


def batches(
    texts: Sequence[str], *, inputs: int = BATCH_INPUTS, tokens: int = BATCH_TOKENS
) -> list[list[int]]:
    """Indexes into `texts`, grouped into requests by count and estimated tokens.

    A text over the token budget still gets a request of its own.
    """
    grouped: list[list[int]] = []
    current: list[int] = []
    size = 0
    for i, text in enumerate(texts):
        cost = estimated_tokens(text)
        if current and (len(current) >= inputs or size + cost > tokens):
            grouped.append(current)
            current, size = [], 0
        current.append(i)
        size += cost
    if current:
        grouped.append(current)
    return grouped
