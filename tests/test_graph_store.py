import subprocess
import sys
import time
from datetime import UTC, date, datetime

import pytest

from nomoscope.graph import GraphError, records, store
from nomoscope.graph.store import Store
from nomoscope.settings import Settings


def registration(record="r1", jurisdiction="us-il-windytown", session=None):
    return records.Registration(
        record=record,
        feed="code",
        jurisdiction=jurisdiction,
        code="municipal",
        session=session,
        completeness="full",
        fetched_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        last_modified=None,
        sha256="a" * 64,
        crunchable=True,
        item_count=1,
        size=10,
    )


def test_a_write_open_creates_the_store_and_a_reopen_keeps_its_rows(tmp_path):
    graph = Store(Settings(data_root=tmp_path))
    assert not graph.exists()
    with graph.open() as conn:
        records.register(conn, "src", [registration()], "1")
    assert graph.exists()
    with graph.open(read_only=True) as conn:
        [row] = records.records(conn, "src")
    assert (row.record, row.code_version, row.crunchable) == ("r1", "1", True)
    assert (row.jurisdiction, row.code, row.session) == (
        "us-il-windytown",
        "municipal",
        None,
    )
    assert row.fetched_at == datetime(2026, 9, 1, 12, tzinfo=UTC)
    assert row.published == (date(2026, 9, 1), "fetched")


def test_records_narrow_by_jurisdiction_across_sources(tmp_path):
    graph = Store(Settings(data_root=tmp_path))
    with graph.open() as conn:
        records.register(conn, "a", [registration("r1"), registration("r2", "us-il-elgin")], "1")
        records.register(conn, "b", [registration("r3", session="2025")], "1")
        found = records.records(conn, jurisdiction="us-il-windytown")
        assert [(r.source, r.record) for r in found] == [("a", "r1"), ("b", "r3")]
        assert found[1].session == "2025"
        assert records.jurisdictions(conn) == ["us-il-elgin", "us-il-windytown"]


def test_a_registration_checks_its_jurisdiction_and_completeness():
    with pytest.raises(Exception, match="jurisdiction"):
        registration(jurisdiction="IL-WindyTown")
    with pytest.raises(ValueError, match="completeness"):
        records.Registration(
            "r",
            "f",
            "us-il",
            "",
            None,
            "most",
            datetime.now(UTC),
            None,
            "a",
            True,
            1,
            1,
        )


def test_a_read_only_open_needs_the_file(tmp_path):
    with (
        pytest.raises(GraphError, match="no graph store"),
        Store(Settings(data_root=tmp_path)).open(read_only=True),
    ):
        pass


def test_a_locked_store_is_retried_then_refused(tmp_path, monkeypatch):
    graph = Store(Settings(data_root=tmp_path))
    with graph.open():
        pass
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            f"import duckdb, sys, time; c = duckdb.connect({str(graph.path)!r});"
            " print('held', flush=True); time.sleep(3)",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        monkeypatch.setattr(store, "LOCK_INTERVAL", 0.05)
        monkeypatch.setattr(store, "LOCK_WAIT", 0.3)
        started = time.monotonic()
        with (
            pytest.raises(GraphError, match="locked by another"),
            graph.open(read_only=True),
        ):
            pass
        assert time.monotonic() - started >= 0.3
        monkeypatch.setattr(store, "LOCK_WAIT", 10.0)
        with graph.open(read_only=True) as conn:
            assert conn.execute("SELECT count(*) FROM records").fetchone() == (0,)
    finally:
        holder.kill()
        holder.wait()
