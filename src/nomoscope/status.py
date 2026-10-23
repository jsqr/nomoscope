"""The status report.

All counts except the archive's come from the graph store, so status
does not read the crunched or derived files.
"""

from dataclasses import dataclass
from pathlib import Path

from nomoscope.crunch import routes as routing
from nomoscope.crunch import runner
from nomoscope.crunch.registry import Registry
from nomoscope.derive import build, registry, segment
from nomoscope.derive import conventions as drafting
from nomoscope.graph import records, steps
from nomoscope.graph.store import Store
from nomoscope.settings import Settings
from nomoscope.snarf import archive


@dataclass(frozen=True)
class SourceStatus:
    source: str
    archived: int
    registered: int
    unrouted: int
    known: bool  # in the registry


@dataclass(frozen=True)
class CruncherStatus:
    cruncher: str
    states: dict[str, int]  # step state -> count


@dataclass(frozen=True)
class JurisdictionStatus:
    jurisdiction: str
    state: str
    crunchers: tuple[str, ...]
    publications: int
    versions: int | None  # None until derived, or without law tables


@dataclass(frozen=True)
class Status:
    data_root: Path
    sources: tuple[SourceStatus, ...]
    crunchers: tuple[CruncherStatus, ...]
    jurisdictions: tuple[JurisdictionStatus, ...]
    catalog: bool


def status(cfg: Settings, known: Registry) -> Status:
    archived = {s: len(archive.record_ids(cfg.snarfed, s)) for s in archive.sources(cfg.snarfed)}
    store = Store(cfg)
    names = sorted(set(archived) | set(known.sources))
    if not store.exists():
        sources = tuple(
            SourceStatus(s, archived.get(s, 0), 0, 0, s in known.sources) for s in names
        )
        return Status(cfg.data_root, sources, (), (), cfg.catalog.is_file())
    routes = routing.load(cfg)
    with store.open(read_only=True) as conn:
        rows = records.records(conn)
        source_rows = []
        for source in sorted(set(names) | {r.source for r in rows}):
            mine = [r for r in rows if r.source == source]
            unrouted = sum(1 for r in mine if r.crunchable and routing.route(routes, r) is None)
            source_rows.append(
                SourceStatus(
                    source, archived.get(source, 0), len(mine), unrouted, source in known.sources
                )
            )
        crunchers = tuple(
            CruncherStatus(name, states)
            for name, states in sorted(runner.pending(cfg, conn, known, routes).items())
        )
        plans = build.plan(conn, routes, drafting.load(cfg), segment.settings(cfg.segments))
        state = steps.classify(cfg, conn, [p.planned for p in plans], check_outputs=False)
        jurisdictions = []
        for p in plans:
            tables = {t.name: t for t in steps.tables(conn, p.planned.key)}
            versions = tables.get(registry.LAW_VERSIONS)
            carves = build.carves(p.planned)
            jurisdictions.append(
                JurisdictionStatus(
                    p.jurisdiction,
                    state[p.planned.key].value,
                    tuple(sorted({i.scope for i in carves})),
                    len(carves),
                    versions.rows if versions is not None else None,
                )
            )
    return Status(
        cfg.data_root,
        tuple(source_rows),
        crunchers,
        tuple(jurisdictions),
        cfg.catalog.is_file(),
    )
