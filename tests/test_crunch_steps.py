"""Crunchers with a snapshot step, records with every code, and route changes."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

import polars as pl

from nomoscope.crunch import runner, schemas
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.results import CrunchResult
from nomoscope.crunch.routes import ANY, Route
from nomoscope.derive import registry as datasets
from nomoscope.graph import steps
from nomoscope.graph.records import RecordRow
from nomoscope.graph.steps import CARVE, CRUNCH, Planned, StepKey
from nomoscope.graph.store import Store
from nomoscope.partition import dataset_path
from nomoscope.settings import Settings
from nomoscope.snarf import archive, local
from nomoscope.snarf.feeds import Feed, Source
from nomoscope.snarf.run import blob_store
from tests import fake
from tests.pipeline import P1, P3, derive, publish

DUMP = Feed("dump", ("",), "none", days=(), completeness="full")
STATE = Source("state", (DUMP,), (), lambda d: {})
LINES = "lines"


@dataclass(frozen=True)
class Staged:
    """Loads each dump into a snapshot table, then carves every code from it."""

    def plan(self, cfg: Settings, name: str, rows: Sequence[RecordRow]) -> list[Planned]:
        loads = runner.record_plan(name, rows, "1", stage=CRUNCH)
        carves = [Planned(StepKey(CARVE, name, p.key.unit), (p.input,), "1") for p in loads]
        return [*loads, *carves]

    def run(
        self, cfg: Settings, store: Store, planned: Planned, rows: Mapping[str, RecordRow]
    ) -> CrunchResult:
        row = rows[planned.key.unit]
        if planned.key.stage == CRUNCH:
            record = archive.find(cfg.snarfed, row.source, row.record)
            [item] = record.items()
            text = blob_store(cfg).read(item.sha256).decode()
            lines = [line.split("|") for line in text.splitlines()[1:]]
            frame = pl.DataFrame(lines, schema=["code", "num", "text"], orient="row")
            tables = runner.write_snapshot(cfg, store, planned, {LINES: frame})
            return CrunchResult(planned.key.scope, CRUNCH, row.record, row.jurisdiction, tables)
        with store.open(read_only=True) as conn:
            [table] = steps.tables(conn, StepKey(CRUNCH, planned.key.scope, row.record))
        frame = pl.read_parquet(cfg.data_root / table.path)
        sections = frame.select(
            pl.lit(row.jurisdiction).alias("jurisdiction"),
            pl.col("code").alias("law_code"),
            pl.col("num").alias("section_num"),
            "text",
        )
        stated = schemas.Stated(date.fromisoformat(text_date(cfg, row)))
        codes = sorted(set(frame["code"]))
        built = {
            schemas.LAW_SECTIONS: sections,
            schemas.LAW_PUBLICATIONS: schemas.publication(row, codes, stated),
        }
        tables = runner.write_carved(cfg, store, planned, built)
        return CrunchResult(planned.key.scope, CARVE, row.record, row.jurisdiction, tables)


def text_date(cfg: Settings, row: RecordRow) -> str:
    record = archive.find(cfg.snarfed, row.source, row.record)
    [item] = record.items()
    return blob_store(cfg).read(item.sha256).decode().splitlines()[0]


STATE_REGISTRY = Registry(
    sources={"state": STATE},
    crunchers={"staged": Staged()},
    routes=(Route("state", "us-ca", ANY, "staged"),),
)


def dump(cfg, tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    out = local.import_path(cfg, STATE, DUMP, path, {"jurisdiction": "us-ca"}, importer="t")
    assert out.record is not None
    return out.record


def read(cfg, dataset, j="us-ca"):
    return pl.read_parquet(dataset_path(cfg, dataset, j))


def test_a_snapshot_step_runs_before_the_carve_and_one_record_carries_two_codes(cfg, tmp_path):
    first = dump(cfg, tmp_path, "a.txt", "2024-01-01\nGOV|1.|Text one.\nPEN|1.|Text two.\n")
    store = Store(cfg)
    results = runner.crunch(cfg, store, STATE_REGISTRY)
    assert [(r.stage, r.unit) for r in results] == [("crunch", first.id), ("carve", first.id)]
    dump(cfg, tmp_path, "b.txt", "2024-06-01\nGOV|1.|Text one.\n")
    assert [r.stage for r in runner.crunch(cfg, store, STATE_REGISTRY)] == ["crunch", "carve"]
    derive(cfg, STATE_REGISTRY)
    versions = read(cfg, datasets.LAW_VERSIONS)
    assert sorted(versions["law_code"]) == ["GOV", "PEN"]
    # The second dump is a full publication of GOV only, so PEN is not absent.
    absent = dict(versions.select("law_code", "absent_from").iter_rows())
    assert absent == {"GOV": None, "PEN": None}
    # A missing snapshot reruns only its own step. The carve's plan is unchanged.
    with store.open(read_only=True) as conn:
        [snapshot] = steps.tables(conn, StepKey(CRUNCH, "staged", first.id))
    (cfg.data_root / snapshot.path).unlink()
    rerun = runner.crunch(cfg, store, STATE_REGISTRY)
    assert [(r.stage, r.unit) for r in rerun] == [("crunch", first.id)]


def test_a_route_change_reruns_only_what_it_moves(cfg, tmp_path, conventions):
    two = replace(
        fake.REGISTRY,
        crunchers={"fake": fake.CRUNCHER, "other": runner.Carver(fake.carve, "1")},
    )
    publish(cfg, tmp_path, P1, "a.txt")
    elgin = publish(cfg, tmp_path, P3, "b.txt", jurisdiction="us-il-elgin")
    store = Store(cfg)
    runner.crunch(cfg, store, two)
    derive(cfg, two)
    moved_routes = (*two.routes, Route("fake", "us-il-elgin", ANY, "other"))
    two = replace(two, routes=moved_routes)
    moved = runner.crunch(cfg, store, two)
    assert [(r.cruncher, r.unit) for r in moved] == [("other", elgin.id)]
    assert [r.jurisdiction for r in derive(cfg, two)] == ["us-il-elgin"]
