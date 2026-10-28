import shutil
from datetime import UTC, date, datetime

import polars as pl
import pytest
from typer.testing import CliRunner

from nomoscope import cli, settings
from nomoscope.crunch import CrunchError, manifests, runner, schemas
from nomoscope.crunch.layout import Layout
from nomoscope.graph import records, steps
from nomoscope.graph.steps import TABULATE, Planned, Staleness, StepKey
from nomoscope.graph.store import Store
from tests import fake

APP = cli.app(fake.REGISTRY, fake.APP)

FIRST = """\
publication 2024-01-15
edition Supp. No. 1
node 1 | Chapter 1 | General Provisions
section 1-1 | Title | Ord. 2019-12, eff. 1-1-2020
This code is the Windytown Code.
section 1-2 | Definitions |
(a) "City" means Windytown.
(b) "Code" means this code.
"""


def row(record="r1", completeness="full", last_modified=None):
    return records.RecordRow(
        record=record,
        feed="code",
        jurisdiction="us-il-windytown",
        code="municipal",
        session=None,
        completeness=completeness,
        fetched_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        last_modified=last_modified,
        sha256="a" * 64,
        crunchable=True,
        item_count=1,
        size=1,
        source="fake",
        code_version="1",
        registered_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
    )


def test_conform_fills_casts_and_orders():
    frame = pl.DataFrame(
        {
            "text": ["x"],
            "section_num": ["1-1"],
            "law_code": ["m"],
            "jurisdiction": ["us-il"],
        }
    )
    got = schemas.conform(schemas.LAW_SECTIONS, frame)
    assert got.columns == schemas.tabulated("UTC")[schemas.LAW_SECTIONS].names()
    assert got["text_sha256"][0] == schemas.text_sha256("x")
    assert got["effective_date"].dtype == pl.Date and got["drafting"][0] is None


def test_conform_refuses_unknown_and_missing_columns():
    with pytest.raises(CrunchError, match="not in the tabulated schema"):
        schemas.conform(schemas.LAW_NODES, pl.DataFrame({"jurisdiction": ["us"], "bogus": [1]}))
    with pytest.raises(CrunchError, match="missing required"):
        schemas.conform(schemas.LAW_SECTIONS, pl.DataFrame({"text": ["x"]}))


def test_a_publication_dates_itself_where_it_says_and_names_the_basis():
    stated = schemas.publication(row(), ["municipal"], schemas.Stated(date(2024, 1, 15), "S1"))
    assert stated.row(0, named=True) | {"law_codes": None} == {
        "publication": "r1",
        "jurisdiction": "us-il-windytown",
        "law_codes": None,
        "published_date": date(2024, 1, 15),
        "published_basis": "stated",
        "completeness": "full",
        "edition": "S1",
        "currency_text": None,
        "currency_date": None,
        "session": None,
    }
    fetched = schemas.publication(row(), ["municipal"]).row(0, named=True)
    assert (fetched["published_date"], fetched["published_basis"]) == (
        date(2026, 9, 1),
        "fetched",
    )
    modified = datetime(2026, 8, 30, tzinfo=UTC)
    header = schemas.publication(row(last_modified=modified), ["municipal"]).row(0, named=True)
    assert (header["published_date"], header["published_basis"]) == (
        date(2026, 8, 30),
        "last_modified",
    )


def test_tabulated_tables_are_partitioned_by_publication_and_named_by_step(tmp_path):
    cfg = settings.Settings(data_root=tmp_path)
    got = Layout("fake").tabulated_table(
        cfg, "law_sections", "2026-09-01T120000Z-aaaaaaaa", "0123456789abcdef" * 4
    )
    assert got == (
        tmp_path
        / "sources/crunched/fake/tabulated/law_sections"
        / "publication=2026-09-01T120000Z-aaaaaaaa/0123456789abcdef.parquet"
    )


def invoke(*args):
    return CliRunner().invoke(APP, list(args))


def imported(tmp_path, text, name="code.txt", **params):
    path = tmp_path / name
    path.write_text(text)
    pairs = [f"-p{k}={v}" for k, v in ({"jurisdiction": "us-il-windytown"} | params).items()]
    out = invoke("snarf", "import", "fake", str(path), "--feed", "code", *pairs)
    assert out.exit_code == 0, out.output


def test_the_fake_source_tabulates_an_import_once(cfg, tmp_path):
    imported(tmp_path, FIRST)
    out = invoke("crunch")
    assert out.exit_code == 0, out.output
    assert "us-il-windytown" in out.output and "3 tables" in out.output
    store = Store(cfg)
    with store.open(read_only=True) as conn:
        [rec] = records.records(conn, "fake")
        [routed] = runner.plans(cfg, conn, fake.REGISTRY, fake.REGISTRY.routes)
        state = steps.classify(cfg, conn, routed.planned)
        tables = steps.tables(conn, StepKey(TABULATE, "fake", rec.record))
    assert set(state.values()) == {Staleness.FRESH}
    assert {t.name for t in tables} == {"law_sections", "law_nodes", "law_publications"}
    sections = pl.read_parquet(cfg.data_root / tables[2].path)
    assert sections["section_num"].to_list() == ["1-1", "1-2"]
    assert sections["text"][1] == '(a) "City" means Windytown.\n(b) "Code" means this code.'
    assert sections["node_treepath"].to_list() == ["1", "1"]
    [pub] = pl.read_parquet(cfg.data_root / tables[1].path).to_dicts()
    assert (pub["published_date"], pub["published_basis"]) == (
        date(2024, 1, 15),
        "stated",
    )
    again = invoke("crunch")
    assert "nothing to crunch" in again.output
    forced = invoke("crunch", "fake", "--record", rec.record)
    assert forced.exit_code == 0 and rec.record in forced.output


def test_a_tabulate_without_its_publication_row_is_refused(cfg):
    planned = Planned(StepKey(TABULATE, "fake", "r1"), (), "1")
    with pytest.raises(CrunchError, match="r1: a tabulation writes law_publications"):
        runner.write_tabulated(cfg, Store(cfg), planned, {})


def tabulated(cfg):
    with Store(cfg).open(read_only=True) as conn:
        [rec] = records.records(conn, "fake")
        [routed] = runner.plans(cfg, conn, fake.REGISTRY, fake.REGISTRY.routes)
        return routed.planned[0], steps.tables(conn, StepKey(TABULATE, "fake", rec.record))


def other_root(cfg, tmp_path, monkeypatch):
    """A second data root holding a copy of the first one's sources/."""
    root = tmp_path / "other"
    shutil.copytree(cfg.sources, root / "sources")
    monkeypatch.setenv(settings.env_data_root(fake.APP), str(root))
    return settings.load(fake.APP)


def test_a_step_writes_its_manifest_after_its_tables(cfg, tmp_path):
    imported(tmp_path, FIRST)
    assert invoke("crunch").exit_code == 0
    planned, tables = tabulated(cfg)
    found = manifests.read(cfg, planned)
    assert found is not None
    assert {t.path for t in found.tables} == {t.path for t in tables}
    assert manifests.path(cfg, planned).name == planned.fingerprint[:16] + ".json"


def test_a_copied_step_is_adopted_not_run(cfg, tmp_path, monkeypatch):
    imported(tmp_path, FIRST)
    assert invoke("crunch").exit_code == 0
    _, tables = tabulated(cfg)
    other = other_root(cfg, tmp_path, monkeypatch)
    out = invoke("crunch")
    assert out.exit_code == 0, out.output
    assert "adopted" in out.output
    _, adopted = tabulated(other)
    assert adopted == tables
    assert invoke("crunch").output.strip() == "nothing to crunch"


def test_a_step_missing_a_table_runs(cfg, tmp_path, monkeypatch):
    imported(tmp_path, FIRST)
    assert invoke("crunch").exit_code == 0
    _, tables = tabulated(cfg)
    other = other_root(cfg, tmp_path, monkeypatch)
    (other.data_root / tables[0].path).unlink()
    out = invoke("crunch")
    assert out.exit_code == 0, out.output
    assert "adopted" not in out.output
    assert (other.data_root / tables[0].path).is_file()


def test_after_a_reset_crunch_adopts_every_step(cfg, tmp_path):
    imported(tmp_path, FIRST)
    assert invoke("crunch").exit_code == 0
    reset = invoke("graph", "reset")
    assert reset.exit_code == 0 and "deleted" in reset.output
    assert not Store(cfg).exists()
    out = invoke("crunch")
    assert out.exit_code == 0 and "adopted" in out.output


def test_a_forced_step_runs_even_with_a_manifest(cfg, tmp_path):
    imported(tmp_path, FIRST)
    assert invoke("crunch").exit_code == 0
    out = invoke("crunch", "--force")
    assert out.exit_code == 0 and "3 tables" in out.output and "adopted" not in out.output
