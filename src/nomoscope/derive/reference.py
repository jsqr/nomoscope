"""The reference datasets.

jurisdictions lists each jurisdiction id with its name, kind, parent, and
Census codes. It is built from the latest Census records in the archive
(`derive/census.py`), and is not built until there are some. Every
jurisdiction that a crunchable record is registered under must be in it.
"""

from collections.abc import Mapping
from dataclasses import dataclass

import polars as pl

from nomoscope.derive import census, registry
from nomoscope.errors import NomoscopeError
from nomoscope.graph import fingerprint, records, steps
from nomoscope.graph.records import RECORD
from nomoscope.graph.steps import REFERENCE, Input, Planned, Staleness, StepKey
from nomoscope.graph.store import Store
from nomoscope.parquet import write_parquet
from nomoscope.partition import check_jurisdiction, reference_path
from nomoscope.settings import Settings
from nomoscope.snarf import archive
from nomoscope.snarf import census as census_source
from nomoscope.snarf.run import blob_store

VERSION = "2"
SCHEMA = census.SCHEMA


class ReferenceError(NomoscopeError):
    pass


@dataclass(frozen=True)
class ReferenceResult:
    dataset: str
    rows: int

    @property
    def summary(self) -> str:
        return f"{self.dataset:24} reference {self.rows:9} rows"


def census_records(cfg: Settings) -> dict[str, archive.Record]:
    """The latest Census record of each feed that has one."""
    found = {}
    for feed in census_source.FEEDS:
        record = archive.latest_record(cfg.snarfed, census_source.ID, feed)
        if record is not None:
            found[feed] = record
    return found


def files(cfg: Settings, found: Mapping[str, archive.Record]) -> dict[str, bytes]:
    store = blob_store(cfg)
    out = {}
    for feed, record in found.items():
        [item] = record.items()
        out[feed] = store.read(item.sha256)
    return out


def jurisdictions(cfg: Settings, found: Mapping[str, archive.Record]) -> pl.DataFrame:
    """The table, with each id checked."""
    frame = census.jurisdictions(files(cfg, found))
    for value in frame["id"]:
        check_jurisdiction(value)
    return frame


def plan(found: Mapping[str, archive.Record]) -> Planned:
    return Planned(
        StepKey(REFERENCE, registry.JURISDICTIONS, ""),
        tuple(
            Input(RECORD, census_source.ID, r.id, r.fetch().sha256)
            for _, r in sorted(found.items())
        ),
        fingerprint.code_version(VERSION, SCHEMA),
    )


def unknown(cfg: Settings, store: Store) -> list[str]:
    """Jurisdictions that registered records name and the table lacks."""
    path = reference_path(cfg, registry.JURISDICTIONS)
    known = set(pl.read_parquet(path, columns=["id"])["id"])
    with store.open(read_only=True) as conn:
        return [j for j in records.jurisdictions(conn) if j not in known]


def build(cfg: Settings, store: Store, *, force: bool = False) -> list[ReferenceResult]:
    """Build the jurisdictions dataset when its Census records change.

    Nothing without Census records. Raises if some feeds have no record, or
    if a registered record's jurisdiction is not in the table.
    """
    found = census_records(cfg)
    if not found:
        return []
    missing = [f for f in census_source.FEEDS if f not in found]
    if missing:
        raise ReferenceError(f"no Census record of {', '.join(missing)}; fetch them")
    planned = plan(found)
    results = []
    with store.open() as conn:
        state = steps.classify(cfg, conn, [planned])[planned.key]
        if state is not Staleness.FRESH or force:
            frame = jurisdictions(cfg, found)
            path = reference_path(cfg, registry.JURISDICTIONS)
            write_parquet(frame, path)
            row = steps.table_row(
                cfg,
                registry.JURISDICTIONS,
                path,
                frame.schema,
                frame.height,
                sha256=fingerprint.file_sha256(path),
            )
            steps.record(conn, planned, [row])
            results.append(ReferenceResult(registry.JURISDICTIONS, frame.height))
    absent = unknown(cfg, store)
    if absent:
        raise ReferenceError(
            f"registered jurisdictions not in the Census table: {', '.join(absent)}"
        )
    return results
