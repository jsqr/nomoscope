"""Draft a grammar with a chat model. The request is billed.

`infer` sends a page sample and `sample.INSTRUCTIONS` to Mistral's API.
The model's `review` is discarded. Needs the `infer` extra and
MISTRAL_API_KEY.
"""

import os
import typing
from dataclasses import dataclass

import httpx2
import polars as pl
from mistralai.client.models import ReasoningEffort
from mistralai.client.types import UNSET
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.mistral import MistralModel
from pydantic_ai.providers.mistral import MistralProvider
from pydantic_ai.settings import ModelSettings

from nomoscope.structure import sample
from nomoscope.structure.grammar import Grammar, Review

KEY_ENV = "MISTRAL_API_KEY"
# Output tokens, reasoning included. A reasoning model can think past 16k
# on a 25k-token sample before it writes any answer.
MAX_TOKENS = 32768
# Some reasoning models accept only low, high, and max, and reason at
# length when the field is omitted.
EFFORT = "low"
# mistralai omits from the request any effort not in this list.
SENDABLE = typing.get_args(typing.get_args(ReasoningEffort)[0])
TEMPERATURE = 0.0
TIMEOUT = 600.0
RETRIES = 2
PROMPT_VERSION = "4"


@dataclass(frozen=True)
class Inferred:
    grammar: Grammar
    pages: list[int]
    model: str
    input_tokens: int
    output_tokens: int
    requests: int = 1


class _EffortModel(MistralModel):
    """A Mistral model that sends a given `reasoning_effort`, or none.

    pydantic-ai sends only "high" or "none".
    """

    def __init__(self, name: str, effort: str | None, **kw) -> None:
        super().__init__(name, **kw)
        self.effort = effort

    # Returns efforts beyond the base's "none" and "high"; that is its purpose.
    def _translate_thinking(self, model_request_parameters):  # pyright: ignore[reportIncompatibleMethodOverride]
        return UNSET if self.effort is None else self.effort


def backend(
    name: str,
    api_key: str,
    http_client: httpx2.AsyncClient | None = None,
    effort: str | None = EFFORT,
) -> Model:
    """Return the model on Mistral's own API.

    The OpenAI-compatible endpoint returns a reasoning model's content as a
    list of chunks, which pydantic-ai's OpenAI adapter rejects. The grammar
    comes back as the arguments of an output tool call.
    """
    if effort is not None and effort not in SENDABLE:
        raise ValueError(f"effort {effort!r} is not one the SDK sends: {', '.join(SENDABLE)}")
    provider = MistralProvider(api_key=api_key, http_client=http_client)
    return _EffortModel(name, effort, provider=provider)


def agent(model: Model, max_tokens: int = MAX_TOKENS) -> Agent[None, Grammar]:
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    return Agent(
        model,
        output_type=Grammar,
        instructions=sample.INSTRUCTIONS,
        retries=RETRIES,
        model_settings=ModelSettings(
            temperature=TEMPERATURE, timeout=TIMEOUT, max_tokens=max_tokens
        ),
    )


def infer(
    lines: pl.DataFrame,
    model: Model,
    name: str,
    pages: list[int],
    max_tokens: int = MAX_TOKENS,
) -> Inferred:
    """Ask the model for the document's grammar, showing it the lines of `pages`."""
    result = agent(model, max_tokens).run_sync(sample.render(lines, pages))
    usage = result.usage
    grammar = result.output.model_copy(update={"review": Review()})
    return Inferred(grammar, pages, name, usage.input_tokens, usage.output_tokens, usage.requests)


def comment(inferred: Inferred) -> str:
    return (
        f"Inferred by {inferred.model}, prompt version {PROMPT_VERSION}.\n"
        f"Sample pages: {' '.join(str(p) for p in inferred.pages)}\n"
        f"Tokens: {inferred.input_tokens} in, {inferred.output_tokens} out."
    )
