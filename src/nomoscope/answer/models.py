"""Chat models that answer questions, by slug.

The `[answers]` table names one by slug. Prices are dollars per token, as
the provider lists them. `settings` are fields sent with every request.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from nomoscope.errors import NomoscopeError
from nomoscope.providers import FIREWORKS, Provider


class AnswerError(NomoscopeError):
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
    settings: Mapping[str, Any] = field(default_factory=dict)

    def dollars(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens * self.dollars_per_input_token
            + output_tokens * self.dollars_per_output_token
        )


# Fireworks' listed rates on 2026-10-28. GLM-5.3 always reasons; Fireworks
# takes the effort as low, medium, or high.
CHAT_MODELS = {
    m.slug: m
    for m in (
        ChatModel(
            "glm-5-3-low",
            FIREWORKS,
            "accounts/fireworks/models/glm-5p3",
            1.4e-6,
            4.4e-6,
            settings={"reasoning_effort": "low"},
        ),
    )
}
DEFAULT_CHAT = "glm-5-3-low"


def chat_model(slug: str) -> ChatModel:
    if slug not in CHAT_MODELS:
        raise AnswerError(f"unknown chat model {slug!r}; known: {', '.join(sorted(CHAT_MODELS))}")
    return CHAT_MODELS[slug]
