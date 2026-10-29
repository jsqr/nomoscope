"""Chat models and JSON-schema completions.

Projects supply their models in `Registry.chat_models`, usually loaded from
a TOML file with one table per slug:

    ["glm-5-3-low"]
    provider = "fireworks"            # a name in providers.PROVIDERS
    name = "accounts/fireworks/models/glm-5p3"
    dollars_per_input_token = 1.4e-6  # as the provider lists them
    dollars_per_output_token = 4.4e-6
    max_tokens = 8192                 # optional: output, reasoning included
    output_per_input = 1.0            # optional: for estimates
    settings = { reasoning_effort = "low" }   # optional

`settings` are added to every request body, in the provider's terms.
"""

import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import httpx

from nomoscope.errors import NomoscopeError
from nomoscope.providers import PROVIDERS, Provider, post

TEMPERATURE = 0.0


class ChatError(NomoscopeError):
    pass


@dataclass(frozen=True)
class ChatModel:
    slug: str
    provider: Provider
    name: str
    dollars_per_input_token: float
    dollars_per_output_token: float
    # Output tokens, reasoning included.
    max_tokens: int = 8192
    # For cost estimates; reasoning tokens included.
    output_per_input: float = 1.0
    settings: Mapping[str, Any] = field(default_factory=dict)

    def dollars(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.dollars_per_input_token
            + output_tokens * self.dollars_per_output_token
        )


REQUIRED = ("provider", "name", "dollars_per_input_token", "dollars_per_output_token")


def load(path: Path) -> dict[str, ChatModel]:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    known = {f.name for f in fields(ChatModel)} - {"slug"}
    found = {}
    for slug, table in raw.items():
        where = f"{path}: [{slug}]"
        if not isinstance(table, dict):
            raise ChatError(f"{where} is not a table")
        unknown = sorted(set(table) - known)
        if unknown:
            raise ChatError(f"{where}: unknown keys {', '.join(unknown)}")
        missing = [k for k in REQUIRED if k not in table]
        if missing:
            raise ChatError(f"{where}: missing {', '.join(missing)}")
        if table["provider"] not in PROVIDERS:
            raise ChatError(
                f"{where}: unknown provider {table['provider']!r};"
                f" known: {', '.join(sorted(PROVIDERS))}"
            )
        try:
            found[slug] = ChatModel(
                slug,
                PROVIDERS[table["provider"]],
                str(table["name"]),
                float(table["dollars_per_input_token"]),
                float(table["dollars_per_output_token"]),
                int(table.get("max_tokens", 8192)),
                float(table.get("output_per_input", 1.0)),
                dict(table.get("settings", {})),
            )
        except (TypeError, ValueError) as e:
            raise ChatError(f"{where}: {e}") from e
    return found


def lookup(models: Mapping[str, ChatModel], slug: str, setting: str) -> ChatModel:
    """`setting` is how errors refer to the slug's source."""
    if not slug:
        raise ChatError(f"{setting} is not set; known models: {', '.join(sorted(models))}")
    if slug not in models:
        raise ChatError(
            f"{setting}: unknown chat model {slug!r}; known: {', '.join(sorted(models))}"
        )
    return models[slug]


@dataclass(frozen=True)
class Completion:
    content: str
    input_tokens: int
    output_tokens: int


def complete(
    client: httpx.Client,
    model: ChatModel,
    messages: Sequence[Mapping[str, str]],
    schema: Mapping[str, Any],
    **retry,
) -> Completion:
    payload = {
        "model": model.name,
        "messages": list(messages),
        "temperature": TEMPERATURE,
        "max_tokens": model.max_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "response", "schema": dict(schema)},
        },
        **model.settings,
    }
    body = post(client, "/chat/completions", payload, **retry)
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise ChatError(f"{model.slug}: no message in the reply") from e
    if not content:
        raise ChatError(f"{model.slug}: an empty reply")
    usage = body.get("usage") or {}
    return Completion(
        content, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    )
