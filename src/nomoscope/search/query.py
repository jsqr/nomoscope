"""The search: candidates in force, ranked lists, fusion, reranking, one hit per section."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from functools import partial
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from nomoscope.catalog import build as catalog
from nomoscope.embed import client, models, store
from nomoscope.embed.models import EmbeddingModel
from nomoscope.errors import NomoscopeError
from nomoscope.search import fulltext
from nomoscope.settings import Settings

INSTRUCTION = "Given a search query, retrieve the passages of laws and regulations that answer it"
# The Qwen3 Embedding report reranks the embedding model's top 100.
DEPTH = 100
RERANK_DEPTH = 50
# Reciprocal rank fusion's constant, as Cormack et al. and most systems use.
RRF_K = 60

Vectorize = Callable[[str], list[float]]
Rerank = Callable[[str, Sequence[str]], list[float]]


class SearchError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Options:
    instruction: str = INSTRUCTION
    depth: int = DEPTH
    rerank_model: str = models.DEFAULT_RERANKER
    rerank_depth: int = RERANK_DEPTH


def options(table: Mapping[str, Any]) -> Options:
    """The `[search]` table, over the defaults."""
    defaults = Options()
    known = set(Options.__dataclass_fields__)
    unknown = sorted(set(table) - known)
    if unknown:
        raise SearchError(
            f"[search] {', '.join(unknown)}: unknown; known: {', '.join(sorted(known))}"
        )
    for key, value in table.items():
        if type(value) is not type(getattr(defaults, key)):
            raise SearchError(f"[search] {key}: expected {type(getattr(defaults, key)).__name__}")
    return replace(defaults, **table)


@dataclass(frozen=True)
class Hit:
    rank: int
    law_code: str
    section_num: str
    path: str
    heading: str | None
    snippet: str
    law_version_id: str
    segment_id: str
    score: float
    vector: float | None = None
    text: float | None = None
    rerank: float | None = None

    @property
    def citation(self) -> str:
        sub = "".join(f"({p})" for p in self.path.split("/")) if self.path else ""
        return f"{self.law_code} {self.section_num}{sub}"


CANDIDATES = f"""
SELECT t.id AS segment_id, t.block_id, t.path, t.body, t.content, t.content_sha256,
       l.law_code, l.section_num, l.heading
FROM {catalog.SEGMENT_TEXTS} t
JOIN law_at($jurisdiction, $on) l ON l.id = t.block_id
WHERE t.jurisdiction = $jurisdiction
"""


def candidates(conn: duckdb.DuckDBPyConnection, jurisdiction: str, on: date) -> pl.DataFrame:
    """The segments of the sections in force on `on`."""
    return conn.sql(CANDIDATES, params={"jurisdiction": jurisdiction, "on": on}).pl()


def stored(cfg: Settings, model: EmbeddingModel, jurisdiction: str) -> list[Path]:
    """The jurisdiction's vector files. Raises if there are none."""
    files = store.files(cfg, model, jurisdiction)
    if not files:
        raise SearchError(
            f"{jurisdiction}: no vectors for {model.key}; run embed, or search with"
            " --no-vector --text"
        )
    return files


def vector_ranked(
    conn: duckdb.DuckDBPyConnection,
    model: EmbeddingModel,
    files: Sequence[Path],
    found: pl.DataFrame,
    vector: list[float],
    depth: int,
) -> list[tuple[str, float]]:
    """Candidate segment ids by inner product with `vector`, best first.

    Stored vectors are unit length, so this is cosine similarity.
    """
    conn.register("candidates", found.select("segment_id", "content_sha256"))
    # Parquet keeps the vectors as lists; the cast makes them fixed-width arrays.
    width = f"FLOAT[{model.dim}]"
    rows = conn.execute(
        f"SELECT c.segment_id, max(array_inner_product(e.embedding::{width}, $v::{width}))"
        " AS score FROM candidates c JOIN read_parquet($files) e"
        " ON e.content_sha256 = c.content_sha256"
        " GROUP BY c.segment_id ORDER BY score DESC, c.segment_id LIMIT $depth",
        {"v": vector, "files": [str(f) for f in files], "depth": depth},
    ).fetchall()
    conn.unregister("candidates")
    return [(sid, float(score)) for sid, score in rows]


def fused(lists: Sequence[Sequence[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: each id scores the sum of 1 / (k + rank) over the lists."""
    scores: dict[str, float] = {}
    for ids in lists:
        for rank, sid in enumerate(ids, start=1):
            scores[sid] = scores.get(sid, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))


def search(
    cfg: Settings,
    query: str,
    jurisdiction: str,
    *,
    on: date | None = None,
    vector: bool = True,
    text: bool = False,
    rerank: bool = False,
    limit: int = 10,
    vectorize: Vectorize | None = None,
    rerank_scores: Rerank | None = None,
) -> list[Hit]:
    """The best `limit` sections for the query, one hit each.

    `vectorize` and `rerank_scores` default to the configured models' API calls.
    """
    if not (vector or text):
        raise SearchError("search needs vectors, full text, or both")
    chosen = options(cfg.table("search"))
    model = models.configured(cfg.table("embeddings"))
    conn = catalog.connect(cfg)
    try:
        found = candidates(conn, jurisdiction, on or date.today())
        if found.height == 0:
            return []
        lists: dict[str, list[tuple[str, float]]] = {}
        if vector:
            # Checked before the query is embedded, which is a billed call.
            files = stored(cfg, model, jurisdiction)
            v = (vectorize or partial(query_vector, model))(
                model.query_text(chosen.instruction, query)
            )
            lists["vector"] = vector_ranked(conn, model, files, found, v, chosen.depth)
    finally:
        conn.close()
    if text:
        allowed = set(found["segment_id"].to_list())
        ranked = fulltext.ranked(cfg, query, jurisdiction, chosen.depth * 10)
        lists["text"] = [(sid, s) for sid, s in ranked if sid in allowed][: chosen.depth]
    order = (
        fused([[sid for sid, _ in ranked] for ranked in lists.values()])
        if len(lists) > 1
        else next(iter(lists.values()))
    )
    rows = {r["segment_id"]: r for r in found.iter_rows(named=True)}
    reranked: dict[str, float] = {}
    if rerank:
        top = [sid for sid, _ in order[: chosen.rerank_depth]]
        score = rerank_scores or partial(
            rerank_with, models.reranker(chosen.rerank_model), chosen.instruction
        )
        reranked = dict(zip(top, score(query, [rows[s]["content"] for s in top]), strict=True))
        order = sorted(((s, reranked[s]) for s in top), key=lambda kv: (-kv[1], kv[0])) + [
            (s, v) for s, v in order[chosen.rerank_depth :]
        ]
    by_list = {name: dict(ranked) for name, ranked in lists.items()}
    hits: list[Hit] = []
    seen: set[str] = set()
    for sid, score in order:
        row = rows[sid]
        if row["block_id"] in seen:
            continue
        seen.add(row["block_id"])
        hits.append(
            Hit(
                rank=len(hits) + 1,
                law_code=row["law_code"],
                section_num=row["section_num"],
                path=row["path"] or "",
                heading=row["heading"],
                snippet=row["body"],
                law_version_id=row["block_id"],
                segment_id=sid,
                score=score,
                vector=by_list.get("vector", {}).get(sid),
                text=by_list.get("text", {}).get(sid),
                rerank=reranked.get(sid),
            )
        )
        if len(hits) == limit:
            break
    return hits


def query_vector(model: EmbeddingModel, text: str) -> list[float]:
    with client.make_client(model.provider) as http:
        [v] = client.embed(http, model, [text])
    return v


def rerank_with(
    model: models.Reranker, instruction: str, query: str, documents: Sequence[str]
) -> list[float]:
    prompts = [model.prompt(instruction, query, d) for d in documents]
    out: list[float] = []
    with client.make_client(model.provider) as http:
        for group in client.batches(prompts):
            out.extend(client.rerank(http, model, [prompts[i] for i in group]))
    return out
