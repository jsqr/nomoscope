"""Lookup results by target id, found or not, in files that are only ever added.

    lookups/jurisdiction=<j>/<time>-<writer>.parquet

<j> is the cited law's jurisdiction, so one lookup serves every code that
cites it. The latest row for a target is the one used. A row recorded under
an older excerpt version is not used, so a better prompt or check looks the
target up again. The results were paid for, so this tier follows the archive's rules.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from nomoscope.coverage import writer_slug
from nomoscope.parquet import write_parquet
from nomoscope.partition import partition_path
from nomoscope.settings import Settings

TIER = "lookups"
SCHEMA = pl.Schema(
    {
        "target_id": pl.String,
        "query": pl.String,
        "found": pl.Boolean,
        "url": pl.String,
        "title": pl.String,
        "scope": pl.String,
        "text": pl.String,
        "model": pl.String,
        "input_tokens": pl.Int64,
        "output_tokens": pl.Int64,
        "searches": pl.Int64,
        "extracts": pl.Int64,
        "dollars": pl.Float64,
        "looked_at": pl.Datetime("us", "UTC"),
        "version": pl.String,
    }
)


def folder(cfg: Settings, jurisdiction: str) -> Path:
    return partition_path(cfg.data_root / TIER, {"jurisdiction": jurisdiction})


def held(cfg: Settings, jurisdiction: str, version: str) -> dict[str, dict[str, Any]]:
    found = sorted(folder(cfg, jurisdiction).glob("*.parquet"))
    if not found:
        return {}
    rows = (
        pl.scan_parquet(found, schema=SCHEMA, missing_columns="insert")
        .filter(pl.col("version") == version)
        .sort("looked_at")
        .collect()
    )
    return {r["target_id"]: r for r in rows.iter_rows(named=True)}


def write(cfg: Settings, jurisdiction: str, row: dict[str, Any], writer: str) -> Path:
    when = row["looked_at"].astimezone(UTC)
    path = folder(cfg, jurisdiction) / (
        f"{when.strftime('%Y-%m-%dT%H%M%S%fZ')}-{writer_slug(writer)}.parquet"
    )
    write_parquet(pl.DataFrame([row], schema=SCHEMA), path)
    return path


def views(cfg: Settings) -> dict[str, str]:
    root = cfg.data_root / TIER
    if not any(root.glob("*/*.parquet")):
        return {}
    return {TIER: (root.relative_to(cfg.data_root) / "*" / "*.parquet").as_posix()}


def now() -> datetime:
    return datetime.now(UTC)
