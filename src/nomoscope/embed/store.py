"""Vectors by content_sha256, in files that are only ever added.

    embeddings/model=<slug>-<dim>/jurisdiction=<j>/<time>-<writer>.parquet

Each file holds `content_sha256` and `embedding` (float32, the model's
width). A file is written under a temporary name and renamed, and never
changed after. Two runs may store the same hash; readers keep one vector
per hash. Vectors are paid for and cannot be rebuilt for free, so this
tier is kept with the archive's rules, not derived's.
"""

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from nomoscope.coverage import writer_slug
from nomoscope.embed.models import EmbeddingModel
from nomoscope.parquet import write_parquet
from nomoscope.partition import partition_path
from nomoscope.settings import Settings

TIER = "embeddings"
HASH = "content_sha256"
VECTOR = "embedding"


def schema(model: EmbeddingModel) -> pl.Schema:
    return pl.Schema({HASH: pl.String, VECTOR: pl.Array(pl.Float32, model.dim)})


def root(cfg: Settings, model: EmbeddingModel) -> Path:
    return partition_path(cfg.data_root / TIER, {"model": model.key})


def folder(cfg: Settings, model: EmbeddingModel, jurisdiction: str) -> Path:
    return partition_path(root(cfg, model), {"jurisdiction": jurisdiction})


def files(cfg: Settings, model: EmbeddingModel, jurisdiction: str) -> list[Path]:
    return sorted(folder(cfg, model, jurisdiction).glob("*.parquet"))


def glob(cfg: Settings, model: EmbeddingModel) -> str | None:
    """The glob over every stored file of the model, relative to the data root."""
    found = root(cfg, model)
    if not any(found.glob("*/*.parquet")):
        return None
    return (found.relative_to(cfg.data_root) / "*" / "*.parquet").as_posix()


def hashes(cfg: Settings, model: EmbeddingModel, jurisdiction: str) -> set[str]:
    stored = files(cfg, model, jurisdiction)
    if not stored:
        return set()
    return set(pl.read_parquet(stored, columns=[HASH])[HASH].to_list())


def vectors(cfg: Settings, model: EmbeddingModel, jurisdiction: str) -> pl.DataFrame:
    """One vector per hash."""
    stored = files(cfg, model, jurisdiction)
    if not stored:
        return pl.DataFrame(schema=schema(model))
    return pl.read_parquet(stored, schema=schema(model)).unique(HASH, keep="first")


def write(
    cfg: Settings,
    model: EmbeddingModel,
    jurisdiction: str,
    content_hashes: list[str],
    embedded: list[list[float]],
    writer: str,
    at: datetime | None = None,
) -> Path:
    """Add one file. Its name is the time and the writer, so it never replaces another."""
    when = (at or datetime.now(UTC)).astimezone(UTC)
    path = folder(cfg, model, jurisdiction) / (
        f"{when.strftime('%Y-%m-%dT%H%M%S%fZ')}-{writer_slug(writer)}.parquet"
    )
    if path.exists():
        raise FileExistsError(path)
    frame = pl.DataFrame({HASH: content_hashes, VECTOR: embedded}, schema=schema(model))
    write_parquet(frame, path)
    return path
