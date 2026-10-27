from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from nomoscope import cli, coverage
from nomoscope.catalog import build as catalog
from nomoscope.derive import reference
from nomoscope.graph.store import Store
from tests import census, fake
from tests.pipeline import P1, publish

J = "us-il-windytown"


def at(day: int) -> datetime:
    return datetime(2026, 1, day, tzinfo=UTC)


def invoke(*args: str) -> str:
    out = CliRunner().invoke(cli.app(fake.REGISTRY, fake.APP), list(args))
    assert out.exit_code == 0, out.output
    return out.output


@pytest.fixture
def jurisdictions(cfg, tmp_path):
    census.write(cfg, tmp_path / "census")
    reference.build(cfg, Store(cfg))


def test_the_latest_row_of_each_jurisdiction_is_in_force(cfg):
    coverage.write(
        cfg,
        [coverage.Entry(J), coverage.Entry("us-il-elgin"), coverage.Entry("us-il-chicago")],
        "jj",
        at(1),
    )
    coverage.write(cfg, [coverage.Entry(J, publisher="fake", url="https://x/1")], "jj", at(2))
    coverage.write(cfg, [coverage.Entry("us-il-elgin", listed=False)], "jj", at(3))
    found = coverage.in_force(cfg)
    assert found["jurisdiction"].to_list() == ["us-il-chicago", J]
    assert found.filter(found["jurisdiction"] == J)["url"].item() == "https://x/1"
    assert coverage.rows(cfg).height == 5


def test_a_change_keeps_the_values_not_given(cfg):
    coverage.write(cfg, [coverage.Entry(J, publisher="fake", url="https://x/1")], "jj", at(1))
    assert coverage.change(cfg, J, url="https://x/2") == coverage.Entry(
        J, publisher="fake", url="https://x/2"
    )
    assert coverage.change(cfg, J, listed=False).url == "https://x/1"
    assert coverage.change(cfg, "us-il-elgin") == coverage.Entry("us-il-elgin")


def test_one_file_holds_one_row_per_jurisdiction(cfg):
    with pytest.raises(coverage.CoverageError, match="more than one row"):
        coverage.write(cfg, [coverage.Entry(J), coverage.Entry(J)], "jj")


@pytest.mark.usefixtures("jurisdictions")
def test_set_refuses_a_jurisdiction_not_in_the_table(cfg):
    out = CliRunner().invoke(cli.app(fake.REGISTRY, fake.APP), ["coverage", "set", "us-il-nowhere"])
    assert out.exit_code == 1
    assert "not in the jurisdictions table" in out.output


@pytest.mark.usefixtures("jurisdictions")
def test_the_list_gives_fetch_times_from_the_archive(cfg, tmp_path):
    invoke("coverage", "set", J, "--publisher", "fake", "--url", "https://x/windytown")
    invoke("coverage", "set", "us-il-elgin")
    record = publish(cfg, tmp_path, P1, "p1.txt")
    out = invoke("coverage", "--state", "il")
    [elgin, windytown] = out.splitlines()[:2]
    assert elgin.split() == ["us-il-elgin", "-", "-", "-", "-"]
    fetched = record.fetch().fetched_at.date().isoformat()
    assert windytown.split() == [J, "fake", fetched, fetched, "https://x/windytown"]
    assert "2 listed, 1 fetched" in out


@pytest.mark.usefixtures("jurisdictions")
def test_the_catalog_view_holds_the_rows_in_force(cfg):
    invoke("coverage", "set", J, "--url", "https://x/1")
    invoke("coverage", "set", "us-il-elgin")
    invoke("coverage", "set", J, "--url", "https://x/2")
    invoke("coverage", "remove", "us-il-elgin")
    catalog.build(cfg)
    conn = catalog.connect(cfg)
    try:
        found = conn.execute("SELECT jurisdiction, url FROM coverage").fetchall()
    finally:
        conn.close()
    assert found == [(J, "https://x/2")]


def test_without_rows_the_catalog_has_no_coverage_view(cfg):
    cfg.data_root.mkdir(parents=True)
    assert coverage.COVERAGE not in catalog.build(cfg).views
