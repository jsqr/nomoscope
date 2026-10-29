"""One chat completion whose reply is a JSON object fitting a schema."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from nomoscope.answer.models import AnswerError, ChatModel
from nomoscope.providers import post

TEMPERATURE = 0.0


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
        raise AnswerError(f"{model.slug}: no message in the reply") from e
    if not content:
        raise AnswerError(f"{model.slug}: an empty reply")
    usage = body.get("usage") or {}
    return Completion(
        content, int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
    )
