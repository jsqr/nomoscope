from datetime import UTC, datetime

import polars as pl
import pytest

from nomoscope.graph import fingerprint, records, steps
from nomoscope.graph.steps import Input, Planned, Staleness, StepKey
from nomoscope.graph.store import Store
from nomoscope.parquet import write_parquet
from nomoscope.settings import Settings


@pytest.fixture
def cfg(tmp_path):
    return Settings(data_root=tmp_path)


@pytest.fixture
def graph(cfg):
    return Store(cfg)


A = Input("record", "src", "r1", "a" * 64)
B = Input("record", "src", "r2", "b" * 64)
WHEN = datetime(2026, 9, 1, 12, tzinfo=UTC)


def planned(unit="r1", inputs=(A, B), code="1", stage="carve", scope="src"):
    return Planned(StepKey(stage, scope, unit), tuple(inputs), code)


def written(cfg, name="t", rows=2):
    path = cfg.data_root / "sources" / "crunched" / "src" / name / "data.parquet"
    frame = pl.DataFrame({"x": list(range(rows))})
    write_parquet(frame, path)
    return steps.table_row(cfg, name, path, frame.schema, frame.height), path


def registration(record, sha256, crunchable=True):
    return records.Registration(
        record, "f", "us-il", "", None, "full", WHEN, None, sha256, crunchable, 1, 10
    )


def test_the_fingerprint_depends_on_input_order_scope_and_code_version():
    assert planned(inputs=(A, B)).fingerprint != planned(inputs=(B, A)).fingerprint
    assert planned(code="1").fingerprint != planned(code="2").fingerprint
    moved = Input("record", "other", "r1", "a" * 64)
    assert planned(inputs=(A,)).fingerprint != planned(inputs=(moved,)).fingerprint
    assert planned().input_fingerprint == planned(code="2").input_fingerprint
    assert planned().input == Input("carve", "src", "r1", planned().fingerprint)


def test_columns_fingerprint_sees_names_and_dtypes():
    a = pl.Schema({"x": pl.Int64, "y": pl.String})
    b = pl.Schema({"x": pl.Int32, "y": pl.String})
    assert fingerprint.columns_fingerprint(a) != fingerprint.columns_fingerprint(b)
    assert fingerprint.columns_fingerprint(["x", "y"]) != fingerprint.columns_fingerprint(a)
    assert fingerprint.code_version("3") == "3"
    assert fingerprint.code_version("3", a).startswith("3.")


def test_the_staleness_states(cfg, graph):
    p = planned()
    table, path = written(cfg)
    with graph.open() as conn:
        assert steps.classify(cfg, conn, [p]) == {p.key: Staleness.MISSING}
        steps.record(conn, p, [table], {"note": 1})
        assert steps.classify(cfg, conn, [p]) == {p.key: Staleness.FRESH}
        assert steps.classify(cfg, conn, [planned(code="2")]) == {p.key: Staleness.CODE}
        changed = planned(inputs=(A, Input("record", "src", "r2", "c" * 64)))
        assert steps.classify(cfg, conn, [changed]) == {p.key: Staleness.INPUT}
        both = planned(inputs=(A,), code="2")
        assert steps.classify(cfg, conn, [both]) == {p.key: Staleness.CODE}
        path.unlink()
        assert steps.classify(cfg, conn, [p]) == {p.key: Staleness.OUTPUT}
        # Without check_outputs, missing files are not detected.
        assert steps.classify(cfg, conn, [p], check_outputs=False) == {p.key: Staleness.FRESH}


def test_scopes_keep_steps_of_one_stage_apart(cfg, graph):
    one = planned(stage="derive", scope="us-il-a", unit="", inputs=(A,))
    two = planned(stage="derive", scope="us-il-b", unit="", inputs=(B,))
    with graph.open() as conn:
        steps.record(conn, one, [])
        assert steps.classify(cfg, conn, [one, two]) == {
            one.key: Staleness.FRESH,
            two.key: Staleness.MISSING,
        }
        assert steps.units(conn, "derive", "us-il-a") == [""]
        assert steps.units(conn, "derive", "us-il-b") == []


def test_a_recorded_table_carries_the_size_of_the_file_written(cfg, graph):
    table, path = written(cfg)
    assert table.bytes == path.stat().st_size
    with graph.open() as conn:
        steps.record(conn, planned(), [table])
        assert steps.tables(conn, StepKey("carve", "src", "r1")) == [table]


def test_recording_a_step_again_replaces_its_edges_and_tables(cfg, graph):
    first, _ = written(cfg, "first")
    second, _ = written(cfg, "second", rows=3)
    with graph.open() as conn:
        steps.record(conn, planned(inputs=(A, B)), [first])
        steps.record(conn, planned(inputs=(B,)), [second])
        key = StepKey("carve", "src", "r1")
        assert steps.inputs_of(conn, key) == [B]
        assert steps.tables(conn, key) == [second]
        assert steps.units(conn, "carve", "src") == ["r1"]
        [row] = steps.stored(conn, "carve", "src").values()
        assert row.fingerprint == planned(inputs=(B,)).fingerprint


def test_a_code_bump_upstream_is_a_changed_input_downstream(cfg, graph):
    up = planned(unit="r1", inputs=(A,), stage="crunch")
    down = planned(stage="derive", scope="us-il", unit="", inputs=(up.input,))
    with graph.open() as conn:
        records.register(conn, "src", [registration("r1", "a" * 64)], "1")
        steps.record(conn, up, [])
        steps.record(conn, down, [])
        assert steps.classify(cfg, conn, [up, down]) == {
            up.key: Staleness.FRESH,
            down.key: Staleness.FRESH,
        }
        bumped = planned(unit="r1", inputs=(A,), stage="crunch", code="2")
        replanned = planned(stage="derive", scope="us-il", unit="", inputs=(bumped.input,))
        assert steps.classify(cfg, conn, [bumped, replanned]) == {
            up.key: Staleness.CODE,
            down.key: Staleness.INPUT,
        }
        changed = conn.execute("SELECT stage, scope, input_stage FROM changed_inputs").fetchall()
        assert changed == []
        steps.record(conn, bumped, [])
        changed = conn.execute("SELECT stage, scope, input_stage FROM changed_inputs").fetchall()
        assert changed == [("derive", "us-il", "crunch")]


def test_registered_records_and_hashed_tables(cfg, graph):
    rows = [
        registration("r1", "a" * 64),
        registration("r2", "b" * 64),
        registration("r3", "c" * 64, crunchable=False),
    ]
    table, _ = written(cfg, "law")
    with graph.open() as conn:
        records.register(conn, "src", rows, "1")
        crunch = planned(unit="r1", inputs=(A,), stage="crunch")
        steps.record(conn, crunch, [table])
        assert steps.tables(conn, crunch.key) == [table]
        assert records.registered(conn, "src") == {"r1": "1", "r2": "1", "r3": "1"}
        assert [r.record for r in records.records(conn, "src", crunchable=False)] == ["r3"]
        assert steps.table_hashes(conn, "crunch") == {}
        hashed = steps.TableRow("law", table.path, 2, table.columns_fingerprint, 10, "d" * 64)
        steps.record(conn, planned(stage="derive", scope="us-il", unit=""), [hashed])
        assert steps.table_hashes(conn, "derive") == {table.path: "d" * 64}
