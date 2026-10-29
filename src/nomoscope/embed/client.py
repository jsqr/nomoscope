"""Calls to an embedding provider: vectors, and reranking scores.

Both go to `POST {base_url}/embeddings`. A vector request asks for the
model's width (`dimensions`); each vector is checked for that width and
normalized again, since the provider does not normalize. A rerank request
sends one prompt per document and asks for the logits of "no" and "yes",
softmaxed (`normalize`); the score is the probability of "yes".
"""

import math
from collections.abc import Callable, Sequence
from typing import Any

import httpx

from nomoscope.embed.models import EmbeddingModel, EmbedError, Reranker
from nomoscope.providers import post
from nomoscope.segments import CHARS_PER_TOKEN

BATCH_INPUTS = 64
BATCH_TOKENS = 16000

Embed = Callable[[Sequence[str]], list[list[float]]]


def estimated_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def embed(
    client: httpx.Client, model: EmbeddingModel, texts: Sequence[str], **retry
) -> list[list[float]]:
    """One request. Unit vectors of the model's width, in the order of `texts`."""
    if not texts:
        return []
    payload = {"model": model.name, "input": list(texts), "dimensions": model.dim}
    body = post(client, "/embeddings", payload, **retry)
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
    found = ordered(post(client, "/embeddings", payload, **retry), model.slug, len(prompts))
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
