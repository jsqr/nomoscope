"""Register, route, and run crunch steps. The same for every cruncher.

Each archived record of a registered source is registered, then routed
to a cruncher. Each cruncher plans steps over its records. A step runs
when it is not fresh, in plan order.
"""

import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import duckdb
import polars as pl

from nomoscope.crunch import routes as routing
from nomoscope.crunch import schemas
from nomoscope.crunch.layout import Layout
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.results import CrunchResult, TableResult
from nomoscope.crunch.routes import Routes
from nomoscope.graph import records as graph_records
from nomoscope.graph import steps
from nomoscope.graph.records import RecordRow, Registration
from nomoscope.graph.steps import CARVE, CRUNCH, Input, Planned, Staleness, StepKey, TableRow
from nomoscope.graph.store import Store
from nomoscope.parquet import write_parquet
from nomoscope.settings import Settings
from nomoscope.snarf import archive

log = logging.getLogger(__name__)


def register(cfg: Settings, store: Store, registry: Registry) -> list[Registration]:
    """Register each archived record not yet registered under its source's version."""
    new: list[Registration] = []
    with store.open() as conn:
        for source in registry.sources.values():
            done = graph_records.registered(conn, source.id)
            found = [
                source.registration(r)
                for r in archive.records(cfg.snarfed, source.id)
                if done.get(r.id) != source.version
            ]
            graph_records.register(conn, source.id, found, source.version)
            new.extend(found)
    return new


def record_plan(
    scope: str,
    rows: Iterable[RecordRow],
    code_version: str,
    *,
    stage: str = CRUNCH,
    extra: Callable[[RecordRow], Iterable[Input]] = lambda r: (),
) -> list[Planned]:
    """One step per record, reading the record and any extra inputs."""
    return [
        Planned(
            StepKey(stage, scope, r.record),
            (steps.record_input(r), *extra(r)),
            code_version,
        )
        for r in rows
    ]


@dataclass(frozen=True)
class Routed:
    name: str
    rows: dict[str, RecordRow]
    planned: tuple[Planned, ...]


def plans(
    cfg: Settings,
    conn: duckdb.DuckDBPyConnection,
    registry: Registry,
    routes: Routes,
    names: Iterable[str] | None = None,
) -> list[Routed]:
    """Each cruncher's plan over its routed records, or only the named crunchers'.

    Raises if a route names a cruncher that is not in the registry.
    """
    by_name = routing.routed(routes, graph_records.records(conn, crunchable=True))
    wanted = set(names) if names is not None else None
    found = []
    for name in sorted(by_name):
        if wanted is not None and name not in wanted:
            continue
        cruncher = registry.cruncher(name)
        rows = by_name[name]
        planned = tuple(cruncher.plan(cfg, name, rows))
        found.append(Routed(name, {r.record: r for r in rows}, planned))
    return found


def crunch(
    cfg: Settings,
    store: Store,
    registry: Registry,
    *,
    names: Iterable[str] | None = None,
    units: Iterable[str] | None = None,
    force: bool = False,
) -> list[CrunchResult]:
    """Register, route, and run every stale step.

    names limits the run to those crunchers. units limits it to those steps
    and runs them even when fresh.
    """
    register(cfg, store, registry)
    routes = routing.load(cfg)
    only = set(units) if units is not None else None
    with store.open(read_only=True) as conn:
        found = plans(cfg, conn, registry, routes, names)
        state = steps.classify(cfg, conn, [p for r in found for p in r.planned])
    results = []
    for r in found:
        cruncher = registry.cruncher(r.name)
        for planned in r.planned:
            if only is not None:
                if planned.key.unit not in only:
                    continue
            elif state[planned.key] is Staleness.FRESH and not force:
                continue
            results.append(cruncher.run(cfg, store, planned, r.rows))
    return results


def pending(
    cfg: Settings, conn: duckdb.DuckDBPyConnection, registry: Registry, routes: Routes
) -> dict[str, dict[str, int]]:
    """Count each cruncher's steps by state, from the store alone.

    "held" counts the carves that derive does not read (`steps.admitted`).
    """
    found = plans(cfg, conn, registry, routes)
    state = steps.classify(cfg, conn, [p for r in found for p in r.planned], check_outputs=False)
    counts: dict[str, dict[str, int]] = {}
    for r in found:
        tally = counts.setdefault(r.name, {})
        for p in r.planned:
            tally[state[p.key].value] = tally.get(state[p.key].value, 0) + 1
        held = sum(1 for s in steps.stored(conn, CARVE, r.name).values() if not steps.admitted(s))
        if held:
            tally["held"] = held
    return counts


def record_step(
    store: Store,
    planned: Planned,
    tables: Iterable[TableRow],
    details: Mapping[str, object] | None = None,
) -> None:
    with store.open() as conn:
        steps.record(conn, planned, tables, details)


def write_tables(
    cfg: Settings,
    store: Store,
    lay: Layout,
    unit: str,
    planned: Planned,
    built: Mapping[str, pl.DataFrame],
    path: Callable[[Settings, str, str], Path],
    details: Mapping[str, object] | None = None,
) -> dict[str, TableResult]:
    tables: dict[str, TableResult] = {}
    rows = []
    for name, frame in built.items():
        target = path(cfg, name, unit)
        write_parquet(frame, target)
        rows.append(steps.table_row(cfg, name, target, frame.schema, frame.height))
        tables[name] = TableResult(
            table=name, rows=frame.height, path=str(target.relative_to(lay.root(cfg)))
        )
        log.info("%s/%s: %d rows", unit, name, frame.height)
    record_step(store, planned, rows, details)
    return tables


def write_snapshot(
    cfg: Settings,
    store: Store,
    planned: Planned,
    built: Mapping[str, pl.DataFrame],
    details: Mapping[str, object] | None = None,
) -> dict[str, TableResult]:
    lay = Layout(planned.key.scope)
    unit = planned.key.unit
    return write_tables(cfg, store, lay, unit, planned, built, lay.snapshot_table, details)


def write_carved(
    cfg: Settings,
    store: Store,
    planned: Planned,
    built: Mapping[str, pl.DataFrame],
    zone: str = "UTC",
    details: Mapping[str, object] | None = None,
) -> dict[str, TableResult]:
    """Conform the carved tables, write them, and record the carve step.

    law_publications is required, since derive dates every row by it.
    """
    unit = planned.key.unit
    if planned.key.stage != CARVE:
        raise schemas.CrunchError(f"{unit}: carved tables are written by a carve step")
    if schemas.LAW_PUBLICATIONS not in built:
        raise schemas.CrunchError(f"{unit}: a carve writes {schemas.LAW_PUBLICATIONS}")
    conformed = {name: schemas.conform(name, frame, zone) for name, frame in built.items()}
    lay = Layout(planned.key.scope)
    return write_tables(cfg, store, lay, unit, planned, conformed, lay.carved_table, details)


Carve = Callable[[Settings, RecordRow, archive.Record], Mapping[str, pl.DataFrame]]


@dataclass(frozen=True)
class Carver:
    """A cruncher with one carve step per record.

    carve returns a record's carved tables. Bump version when carve output
    changes for the same record.
    """

    carve: Carve
    version: str
    zone: str = "UTC"

    def plan(self, cfg: Settings, name: str, rows: Sequence[RecordRow]) -> list[Planned]:
        version = schemas.carve_version(self.version, self.zone)
        return record_plan(name, rows, version, stage=CARVE)

    def run(
        self,
        cfg: Settings,
        store: Store,
        planned: Planned,
        rows: Mapping[str, RecordRow],
    ) -> CrunchResult:
        row = rows[planned.key.unit]
        record = archive.find(cfg.snarfed, row.source, row.record)
        tables = write_carved(cfg, store, planned, self.carve(cfg, row, record), self.zone)
        return CrunchResult(planned.key.scope, CARVE, row.record, row.jurisdiction, tables)
