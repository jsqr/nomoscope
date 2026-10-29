"""The extraction store, keyed by unit hash. Files are only added.

    extractions/task=<task>/model=<slug>/jurisdiction=<j>/<time>-<writer>.parquet

One row per item, with its span in the unit's body. A unit with no items has
one row with a null item; a unit the model could not answer has one row with
the reason in `failed`. Token counts and `extra` are on a unit's first row
only. A higher `attempt` supersedes a lower one.

The items were paid for, so this tier follows the archive's rules rather
than derived's.
"""

from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from nomoscope.chat import ChatModel
from nomoscope.coverage import writer_slug
from nomoscope.parquet import write_parquet
from nomoscope.partition import partition_path
from nomoscope.settings import Settings

TIER = "extractions"
HASH = "content_sha256"
SCHEMA = pl.Schema(
    {
        HASH: pl.String,
        "prompt_version": pl.String,
        "ordinal": pl.Int64,
        "item": pl.String,
        "start_char": pl.Int64,
        "end_char": pl.Int64,
        "extra": pl.String,
        "input_tokens": pl.Int64,
        "output_tokens": pl.Int64,
        "answered_by": pl.String,
        "failed": pl.String,
        "attempt": pl.Int64,
    }
)


def folder(cfg: Settings, task: str, model: ChatModel, jurisdiction: str) -> Path:
    keys = {"task": task, "model": model.slug, "jurisdiction": jurisdiction}
    return partition_path(cfg.data_root / TIER, keys)


def files(cfg: Settings, task: str, model: ChatModel, jurisdiction: str) -> list[Path]:
    return sorted(folder(cfg, task, model, jurisdiction).glob("*.parquet"))


def held(
    cfg: Settings, task: str, model: ChatModel, jurisdiction: str, prompt_version: str
) -> pl.DataFrame:
    stored = files(cfg, task, model, jurisdiction)
    if not stored:
        return pl.DataFrame(schema=SCHEMA)
    return (
        pl.scan_parquet(stored, schema=SCHEMA, missing_columns="insert")
        .filter(pl.col("prompt_version") == prompt_version)
        .filter(pl.col("attempt") == pl.col("attempt").max().over(HASH))
        .collect()
    )


def write(
    cfg: Settings,
    task: str,
    model: ChatModel,
    jurisdiction: str,
    rows: pl.DataFrame,
    writer: str,
    at: datetime | None = None,
) -> Path:
    when = (at or datetime.now(UTC)).astimezone(UTC)
    path = folder(cfg, task, model, jurisdiction) / (
        f"{when.strftime('%Y-%m-%dT%H%M%S%fZ')}-{writer_slug(writer)}.parquet"
    )
    write_parquet(rows.select(SCHEMA.names()).cast(SCHEMA), path)
    return path
