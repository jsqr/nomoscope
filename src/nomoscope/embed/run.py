"""Embed a jurisdiction's pending segments, within a spend limit.

Pending: the distinct content hashes of its segments that the store lacks
for the model. They are embedded in segment order, as many as the limit
pays for at the estimated token count. Vectors are written every `flush`
segments, so a run that stops keeps what it paid for.
"""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from nomoscope.embed import store, text
from nomoscope.embed.client import batches
from nomoscope.embed.models import EmbeddingModel
from nomoscope.segments import CHARS_PER_TOKEN
from nomoscope.settings import Settings

FLUSH = 20_000
CONCURRENCY = 4

Embed = Callable[[Sequence[str]], list[list[float]]]


def pending(cfg: Settings, model: EmbeddingModel, jurisdiction: str) -> pl.DataFrame:
    """`content_sha256`, `content`, and `tokens` for each hash not yet stored."""
    found = text.segments(cfg, jurisdiction).unique(store.HASH, keep="first", maintain_order=True)
    done = store.hashes(cfg, model, jurisdiction)
    todo = found.filter(~pl.col(store.HASH).is_in(list(done)))
    return todo.select(
        store.HASH,
        "content",
        # As client.estimated_tokens: characters / CHARS_PER_TOKEN, rounded up.
        (pl.col("content").str.len_chars() / CHARS_PER_TOKEN).ceil().cast(pl.Int64).alias("tokens"),
    )


@dataclass(frozen=True)
class Embedded:
    jurisdiction: str
    pending: int
    tokens: int
    dollars: float
    embedded: int = 0
    spent: float = 0.0
    files: tuple[Path, ...] = ()

    @property
    def summary(self) -> str:
        line = (
            f"{self.jurisdiction:28} {self.pending:7} pending  {self.tokens:10} tokens"
            f"  ${self.dollars:.4f}"
        )
        return line + (f"  embedded {self.embedded} for ${self.spent:.4f}" if self.embedded else "")


def estimate(model: EmbeddingModel, todo: pl.DataFrame, jurisdiction: str) -> Embedded:
    tokens = int(todo["tokens"].sum()) if todo.height else 0
    return Embedded(jurisdiction, todo.height, tokens, tokens * model.dollars_per_token)


def affordable(tokens: Sequence[int], dollars_per_token: float, spend: float) -> int:
    """How many of the leading items the spend pays for."""
    total = 0
    for i, t in enumerate(tokens):
        total += t
        if total * dollars_per_token > spend:
            return i
    return len(tokens)


def embed_all(
    texts: Sequence[str], embed: Embed, concurrency: int = CONCURRENCY
) -> list[list[float]]:
    groups = batches(texts)

    def one(group: list[int]) -> list[list[float]]:
        return embed([texts[i] for i in group])

    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        answers = list(pool.map(one, groups))
    out: list[list[float]] = [[] for _ in texts]
    for group, vectors in zip(groups, answers, strict=True):
        for i, v in zip(group, vectors, strict=True):
            out[i] = v
    return out


def embed_pending(
    cfg: Settings,
    model: EmbeddingModel,
    jurisdiction: str,
    todo: pl.DataFrame,
    *,
    embed: Embed,
    spend: float,
    writer: str,
    flush: int = FLUSH,
    concurrency: int = CONCURRENCY,
) -> Embedded:
    """Embed what `spend` pays for and store it. Returns the estimate with what was done."""
    planned = estimate(model, todo, jurisdiction)
    count = affordable(todo["tokens"].to_list(), model.dollars_per_token, spend)
    written: list[Path] = []
    for start in range(0, count, flush):
        chunk = todo.slice(start, min(flush, count - start))
        vectors = embed_all(chunk["content"].to_list(), embed, concurrency)
        written.append(
            store.write(cfg, model, jurisdiction, chunk[store.HASH].to_list(), vectors, writer)
        )
    spent = int(todo["tokens"].head(count).sum()) * model.dollars_per_token if count else 0.0
    return Embedded(
        jurisdiction,
        planned.pending,
        planned.tokens,
        planned.dollars,
        count,
        spent,
        tuple(written),
    )
