"""Coverage: the jurisdictions a project collects codes for, with each code's publisher and URL.

The table is under `$data_root/coverage/`. Each file holds the rows
written together, and files are only added. A row says whether a
jurisdiction is listed or removed, and gives its publisher and URL as
they were known then. The latest row of a jurisdiction is the one in
force, so the table keeps every change.

A publisher is a source id when the project has a source for it, and
otherwise a name.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from nomoscope.errors import NomoscopeError
from nomoscope.parquet import write_parquet
from nomoscope.partition import check_jurisdiction
from nomoscope.settings import Settings

COVERAGE = "coverage"
SCHEMA = pl.Schema(
    {
        "jurisdiction": pl.String,
        "listed": pl.Boolean,
        "publisher": pl.String,
        "url": pl.String,
        "written_by": pl.String,
        "written_at": pl.Datetime("us"),
        "note": pl.String,
    }
)


class CoverageError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Entry:
    jurisdiction: str
    listed: bool = True
    publisher: str | None = None
    url: str | None = None
    note: str | None = None


def root(cfg: Settings) -> Path:
    return cfg.data_root / COVERAGE


def table_glob(cfg: Settings) -> str | None:
    """The glob over the table's files, relative to the data root. None if it has no files."""
    if not any(root(cfg).glob("*.parquet")):
        return None
    return (root(cfg).relative_to(cfg.data_root) / "*.parquet").as_posix()


def rows(cfg: Settings) -> pl.DataFrame:
    """Every row written."""
    files = sorted(root(cfg).glob("*.parquet"))
    if not files:
        return pl.DataFrame(schema=SCHEMA)
    return pl.read_parquet(files, schema=SCHEMA)


def latest(frame: pl.DataFrame) -> pl.DataFrame:
    """Each jurisdiction's latest row, listed or removed."""
    return (
        frame.sort("written_at", maintain_order=True)
        .unique("jurisdiction", keep="last")
        .sort("jurisdiction")
    )


def in_force(cfg: Settings) -> pl.DataFrame:
    """The listed jurisdictions, each with its latest row."""
    return latest(rows(cfg)).filter(pl.col("listed"))


def current(cfg: Settings, jurisdiction: str) -> Entry | None:
    """The jurisdiction's latest row, listed or removed. None if it has none."""
    found = latest(rows(cfg)).filter(pl.col("jurisdiction") == jurisdiction)
    if found.is_empty():
        return None
    r = found.row(0, named=True)
    return Entry(r["jurisdiction"], r["listed"], r["publisher"], r["url"], r["note"])


def change(
    cfg: Settings,
    jurisdiction: str,
    *,
    listed: bool = True,
    publisher: str | None = None,
    url: str | None = None,
    note: str | None = None,
) -> Entry:
    """The jurisdiction's next row: its latest, with the values given."""
    was = current(cfg, jurisdiction) or Entry(jurisdiction)
    return replace(
        was,
        listed=listed,
        publisher=publisher if publisher is not None else was.publisher,
        url=url if url is not None else was.url,
        note=note,
    )


def write(cfg: Settings, entries: Sequence[Entry], by: str, at: datetime | None = None) -> Path:
    """Write the entries as one file, named by the time they were written."""
    if not entries:
        raise CoverageError("nothing to write")
    for e in entries:
        check_jurisdiction(e.jurisdiction)
    repeated = [j for j, n in Counter(e.jurisdiction for e in entries).items() if n > 1]
    if repeated:
        raise CoverageError(f"more than one row for {', '.join(sorted(repeated))}")
    when = (at or datetime.now(UTC)).astimezone(UTC)
    path = root(cfg) / f"{when.strftime('%Y-%m-%dT%H%M%S%fZ')}.parquet"
    if path.exists():
        raise CoverageError(f"{path} exists")
    frame = pl.DataFrame(
        [
            {
                "jurisdiction": e.jurisdiction,
                "listed": e.listed,
                "publisher": e.publisher,
                "url": e.url,
                "written_by": by,
                "written_at": when.replace(tzinfo=None),
                "note": e.note,
            }
            for e in entries
        ],
        schema=SCHEMA,
    )
    write_parquet(frame, path)
    return path
