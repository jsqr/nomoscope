"""Build each jurisdiction's derived datasets from its tabulated publications.

There is one derive step per jurisdiction. Its inputs are the tabulate steps
of the jurisdiction's records, from every source, and the drafting
conventions. A record's tabulate step is the one of the cruncher it routes
to now. A tabulation that records `admitted: false` in its details is held:
it is not an input until a later tabulate of the record admits it.

If the only change is new publications, they are folded onto the stored
datasets, and subdivisions and segments of unchanged versions are kept.
Any other change refolds every publication. Both give the same result.
The datasets are written to derived/<dataset>/jurisdiction=<j>/.
"""

import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import duckdb
import polars as pl

from nomoscope.crunch import routes as routing
from nomoscope.crunch import schemas as tabulated_schemas
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.routes import Routes
from nomoscope.derive import conventions as drafting
from nomoscope.derive import datasets, registry, segment
from nomoscope.derive.conventions import Conventions
from nomoscope.derive.datasets import Reuse, Tabulated
from nomoscope.graph import fingerprint, records, steps
from nomoscope.graph.steps import CONFIG, DERIVE, TABULATE, Input, Planned, Staleness, StepKey
from nomoscope.graph.store import Store
from nomoscope.parquet import write_parquet
from nomoscope.partition import dataset_path
from nomoscope.settings import Settings

log = logging.getLogger(__name__)

# Bump when the same tabulated tables give different datasets.
VERSION = "4"
FOLD = "fold"
REFOLD = "refold"


def code_version(settings: segment.Settings) -> str:
    return fingerprint.code_version(f"{VERSION}-{settings.tag}", *datasets.SCHEMAS.values())


def conventions_input(found: Conventions) -> Input:
    return Input(CONFIG, drafting.SCOPE, "", found.fingerprint)


@dataclass(frozen=True)
class JurisdictionStep:
    planned: Planned
    jurisdiction: str


@dataclass(frozen=True)
class BuildResult:
    jurisdiction: str
    mode: str
    publications: int
    tables: dict[str, int]

    @property
    def summary(self) -> str:
        return (
            f"{self.jurisdiction:24} {self.mode:6} {self.publications:4} publication(s)"
            f"  {len(self.tables)} datasets {sum(self.tables.values()):9} rows"
        )


def plan(
    conn: duckdb.DuckDBPyConnection,
    routes: Routes,
    found: Conventions = drafting.NONE,
    settings: segment.Settings = segment.DEFAULT,
) -> list[JurisdictionStep]:
    version = code_version(settings)
    tabulations: dict[str, dict[str, steps.Stored]] = {}
    by_jurisdiction: dict[str, list[Input]] = defaultdict(list)
    rows = records.records(conn, crunchable=True)
    for name, routed in routing.routed(routes, rows).items():
        stored = tabulations.setdefault(name, steps.stored(conn, TABULATE, name))
        for row in routed:
            tabulate = stored.get(row.record)
            if tabulate is not None and steps.admitted(tabulate):
                by_jurisdiction[row.jurisdiction].append(
                    Input(TABULATE, name, row.record, tabulate.fingerprint)
                )
    config = conventions_input(found)
    plans = []
    for j, inputs in sorted(by_jurisdiction.items()):
        inputs.sort(key=lambda i: (i.unit, i.scope))
        key = StepKey(DERIVE, j, "")
        plans.append(JurisdictionStep(Planned(key, (*inputs, config), version), j))
    return plans


def tabulations(planned: Planned) -> list[Input]:
    return [i for i in planned.inputs if i.stage == TABULATE]


def added(planned: Planned, state: Staleness, previous: Sequence[Input]) -> list[Input] | None:
    """The new publications to fold onto the stored datasets.

    None if the datasets must be refolded: the step is stale for another
    reason, or a publication it folded has changed or gone.
    """
    if state is not Staleness.INPUT or not previous:
        return None
    now = {(i.stage, i.scope, i.unit): i.fingerprint for i in planned.inputs}
    if any(now.get((i.stage, i.scope, i.unit)) != i.fingerprint for i in previous):
        return None
    folded = {(i.stage, i.scope, i.unit) for i in previous}
    new = [i for i in tabulations(planned) if (i.stage, i.scope, i.unit) not in folded]
    return new or None


def build(
    cfg: Settings,
    store: Store,
    known: Registry,
    jurisdictions: Iterable[str] | None = None,
    *,
    force: bool = False,
) -> list[BuildResult]:
    """Build each stale jurisdiction, or only those named. force builds all of them."""
    if not store.exists():
        return []
    settings = known.segments
    found = drafting.load(known.conventions)
    routes = known.routes
    wanted = set(jurisdictions) if jurisdictions is not None else None
    with store.open(read_only=True) as conn:
        plans = [
            p
            for p in plan(conn, routes, found, settings)
            if wanted is None or p.jurisdiction in wanted
        ]
        state = steps.classify(cfg, conn, [p.planned for p in plans])
        previous = {p.planned.key: steps.inputs_of(conn, p.planned.key) for p in plans}
        sources = {(r.record, r.source) for r in records.records(conn, crunchable=True)}
        tabulated = {
            (i.scope, i.unit): steps.tables(conn, StepKey(TABULATE, i.scope, i.unit))
            for p in plans
            for i in tabulations(p.planned)
        }
    source_of = dict(sources)
    results = []
    for p in plans:
        st = state[p.planned.key]
        if st is Staleness.FRESH and not force:
            continue
        new = None if force else added(p.planned, st, previous[p.planned.key])
        results.append(build_one(cfg, store, p, new, tabulated, source_of, settings, found))
    return results


def build_one(
    cfg: Settings,
    store: Store,
    step: JurisdictionStep,
    new: list[Input] | None,
    tabulated: Mapping[tuple[str, str], list[steps.TableRow]],
    source_of: Mapping[str, str],
    settings: segment.Settings,
    found: Conventions,
) -> BuildResult:
    j = step.jurisdiction
    stored = read_stored(cfg, j) if new is not None else None
    if new is not None and stored is not None:
        mode, todo = FOLD, new
        onto = datasets.stored_ledgers(stored)
        reuse = Reuse(stored.get(registry.LAW_SUBDIVISIONS), stored.get(registry.SEGMENTS))
    else:
        mode, todo, onto, reuse = REFOLD, tabulations(step.planned), None, Reuse()
    publications = [
        load_tabulated(cfg, source_of[i.unit], tabulated[i.scope, i.unit]) for i in todo
    ]
    ledgers = datasets.folded(publications, j, onto)
    built = datasets.datasets(ledgers, j, settings, reuse, found)
    rows = write_tables(cfg, built, j)
    with store.open() as conn:
        steps.record(conn, step.planned, rows, {"mode": mode, "publications": len(todo)})
    log.info("%s: %s of %d publication(s)", j, mode, len(todo))
    return BuildResult(j, mode, len(todo), {k: v.height for k, v in built.items()})


def load_tabulated(cfg: Settings, source: str, tables: Iterable[steps.TableRow]) -> Tabulated:
    frames = {t.name: pl.read_parquet(cfg.data_root / t.path) for t in tables}
    return Tabulated(
        source=source,
        publication=frames[tabulated_schemas.LAW_PUBLICATIONS],
        sections=frames.get(tabulated_schemas.LAW_SECTIONS),
        nodes=frames.get(tabulated_schemas.LAW_NODES),
        sessions=frames.get(tabulated_schemas.SESSIONS),
    )


def read_stored(cfg: Settings, jurisdiction: str) -> dict[str, pl.DataFrame] | None:
    """The jurisdiction's stored datasets.

    None if publications is missing, or if only some of a family's datasets
    exist.
    """
    found = {}
    for family in (registry.COMMON_DATASETS, registry.LAW_DATASETS):
        paths = {d: dataset_path(cfg, d, jurisdiction) for d in family}
        present = {d for d, p in paths.items() if p.is_file()}
        required = family is registry.COMMON_DATASETS
        if (present or required) and present != set(family):
            return None
        found.update({d: pl.read_parquet(paths[d]) for d in sorted(present)})
    return found


def write_tables(
    cfg: Settings, built: Mapping[str, pl.DataFrame], jurisdiction: str
) -> list[steps.TableRow]:
    """Write and hash each dataset.

    Removes the file of any dataset not built, such as the law datasets of a
    jurisdiction that no longer has law tables.
    """
    for dataset in set(registry.DATASETS) - set(built):
        dataset_path(cfg, dataset, jurisdiction).unlink(missing_ok=True)
    rows = []
    for dataset, frame in built.items():
        path = dataset_path(cfg, dataset, jurisdiction)
        write_parquet(frame, path)
        sha256 = fingerprint.file_sha256(path)
        rows.append(steps.table_row(cfg, dataset, path, frame.schema, frame.height, sha256=sha256))
    return rows
