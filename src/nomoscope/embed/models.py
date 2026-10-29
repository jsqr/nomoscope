"""Embedding and reranking models by slug, and the providers that serve them.

Settings name a model by slug (`[embeddings] model`, `[search]
rerank_model`); the providers are in `nomoscope.providers`. Prices are
dollars per token, as the provider lists them.

Qwen3-Embedding is a Matryoshka model: the first `dim` values of a vector,
normalized again, are an embedding of their own. A query carries an
English instruction in the model's template; a document is embedded bare.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from nomoscope.errors import NomoscopeError
from nomoscope.providers import FIREWORKS, Provider


class EmbedError(NomoscopeError):
    pass


@dataclass(frozen=True)
class EmbeddingModel:
    slug: str
    provider: Provider
    name: str
    # The width stored; requested from the provider and checked on return.
    dim: int
    max_dim: int
    dollars_per_token: float
    query_template: str = "{query}"

    def query_text(self, instruction: str, query: str) -> str:
        return self.query_template.format(instruction=instruction, query=query)

    @property
    def key(self) -> str:
        """The model and width, which name the store's partition."""
        return f"{self.slug}-{self.dim}"


@dataclass(frozen=True)
class Reranker:
    slug: str
    provider: Provider
    name: str
    dollars_per_token: float
    template: str
    # Token ids of "no" and "yes", whose logits the provider returns.
    logits: tuple[int, int]

    def prompt(self, instruction: str, query: str, document: str) -> str:
        return self.template.format(instruction=instruction, query=query, document=document)


# The query template from the Qwen3-Embedding model card; no space after "Query:".
QWEN3_QUERY = "Instruct: {instruction}\nQuery:{query}"
# The Qwen3-Reranker model card's prompt: system prompt, the pair, and an
# empty thinking block, so the next token is "yes" or "no".
QWEN3_RERANK = (
    "<|im_start|>system\nJudge whether the Document meets the requirements based on the"
    ' Query and the Instruct provided. Note that the answer can only be "yes" or "no".'
    "<|im_end|>\n<|im_start|>user\n"
    "<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {document}"
    "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
)

EMBEDDING_MODELS = {
    m.slug: m
    for m in (
        EmbeddingModel(
            "qwen3-embedding-8b",
            FIREWORKS,
            "fireworks/qwen3-embedding-8b",
            1024,
            4096,
            1e-7,
            QWEN3_QUERY,
        ),
    )
}
RERANKERS = {
    m.slug: m
    for m in (
        Reranker(
            "qwen3-reranker-8b",
            FIREWORKS,
            "fireworks/qwen3-reranker-8b",
            2e-7,
            QWEN3_RERANK,
            (2753, 9454),
        ),
    )
}
DEFAULT_EMBEDDING = "qwen3-embedding-8b"
DEFAULT_RERANKER = "qwen3-reranker-8b"


def embedding_model(slug: str, dim: int | None = None) -> EmbeddingModel:
    """The model, at `dim` dimensions when given."""
    if slug not in EMBEDDING_MODELS:
        known = ", ".join(sorted(EMBEDDING_MODELS))
        raise EmbedError(f"unknown embedding model {slug!r}; known: {known}")
    found = EMBEDDING_MODELS[slug]
    if dim is None:
        return found
    if not isinstance(dim, int) or not 0 < dim <= found.max_dim:
        raise EmbedError(f"{slug}: dimensions must be 1 to {found.max_dim}, not {dim}")
    return replace(found, dim=dim)


def reranker(slug: str) -> Reranker:
    if slug not in RERANKERS:
        raise EmbedError(f"unknown reranker {slug!r}; known: {', '.join(sorted(RERANKERS))}")
    return RERANKERS[slug]


EMBEDDINGS_KEYS = frozenset({"model", "dimensions"})


def configured(table: Mapping[str, Any]) -> EmbeddingModel:
    """The model the `[embeddings]` table names."""
    unknown = sorted(set(table) - EMBEDDINGS_KEYS)
    if unknown:
        raise EmbedError(f"[embeddings] {', '.join(unknown)}: unknown; known: dimensions, model")
    return embedding_model(table.get("model", DEFAULT_EMBEDDING), table.get("dimensions"))
