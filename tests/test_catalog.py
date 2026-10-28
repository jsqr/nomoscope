import json
from datetime import date

import pytest
from typer.testing import CliRunner

from nomoscope import cli
from nomoscope.catalog import build as catalog
from nomoscope.catalog import cite
from tests import census, fake
from tests.pipeline import P1, P2, P3

pytestmark = pytest.mark.usefixtures("conventions")
J = "us-il-windytown"
# Section 1-1 replaced by a stub that states its repeal.
P4 = P3.replace("publication 2025-01-10", "publication 2025-06-01").replace(
    "section 1-1 | Title | Ord. 2019-12, passed 12-3-2019, eff. 1-1-2020\n"
    "This code is the Windytown Code.",
    "section 1-1 | Title | Repealed by Ord. 2025-9, eff. 7-1-2025\nRepealed.",
)


def invoke(*args):
    # Built per call, after the conventions fixture has set the registry.
    out = CliRunner().invoke(cli.app(fake.REGISTRY, fake.APP), list(args))
    assert out.exit_code == 0, out.output
    return out.output


def publish(tmp_path, text, name, *params):
    path = tmp_path / name
    path.write_text(text)
    invoke(
        "snarf",
        "import",
        "fake",
        str(path),
        "-f",
        "code",
        f"-pjurisdiction={J}",
        *params,
    )


def query(sql):
    return [json.loads(line) for line in invoke("sql", sql, "--format", "json").splitlines()]


def law_at(on):
    rows = query(f"SELECT * FROM law_at('{J}', DATE '{on}') ORDER BY section_num")
    return {r["section_num"]: r for r in rows}


@pytest.fixture
def three(cfg, tmp_path):
    census.write(cfg, tmp_path / "census")
    publish(tmp_path, P1, "p1.txt")
    publish(tmp_path, P2, "p2.txt", "-pcompleteness=partial")
    publish(tmp_path, P3, "p3.txt")
    invoke("crunch")
    out = invoke("derive")
    assert "catalog" in out
    return tmp_path


def test_the_law_as_it_stood_on_each_date(three):
    # Stated effective dates are earlier than the first publication.
    assert sorted(law_at(date(2023, 6, 1))) == ["1-1", "1-2", "2-1"]
    between = law_at(date(2024, 3, 1))
    assert '"City" means Windytown.' in between["1-2"]["text"]
    assert between["1-2"]["end_basis"] == "successor"
    after = law_at(date(2024, 7, 1))
    assert '"Person" includes' in after["1-2"]["text"]
    assert after["1-2"]["start_basis"] == "effective"
    assert after["1-2"]["start_date"] == "2024-06-01"
    assert sorted(law_at(date(2025, 2, 1))) == ["1-1", "1-2"]


def test_a_section_the_census_lost_ends_absent(three):
    [dropped] = query(f"SELECT * FROM law_in_force WHERE section_id = '{J}/law/municipal/2-1'")
    assert (dropped["absent_from"], dropped["end_date"], dropped["end_basis"]) == (
        "2025-01-10",
        "2025-01-10",
        "absent",
    )


def test_a_rerun_does_nothing_and_a_fourth_publication_folds(three):
    assert "nothing to crunch" in invoke("crunch")
    assert "nothing to derive" in invoke("derive")
    publish(three, P4, "p4.txt")
    invoke("crunch")
    out = invoke("derive")
    assert f"{J}" in out and "fold" in out and "refold" not in out
    assert sorted(law_at(date(2025, 6, 15))) == ["1-1", "1-2"]
    later = law_at(date(2025, 8, 1))
    assert sorted(later) == ["1-2"]
    title = query(
        f"SELECT * FROM law_in_force WHERE section_id = '{J}/law/municipal/1-1' ORDER BY start_date"
    )
    # The text ends when the stub takes effect. The stub is repealed the same
    # day, so it is never in force.
    assert [(r["end_basis"], r["end_date"]) for r in title] == [
        ("successor", "2025-07-01"),
        ("repeal", "2025-07-01"),
    ]


def test_a_session_ends_on_its_last_publication(cfg, tmp_path):
    publish(tmp_path, P1, "p1.txt")
    invoke("crunch")
    invoke("derive")
    conn = catalog.connect(cfg)
    try:
        none = conn.execute(f"SELECT count(*) FROM law_at_session('{J}', '2025')").fetchone()
    finally:
        conn.close()
    assert none == (0,)


def test_status_and_graph_show_report_the_steps(three):
    out = invoke("status")
    assert "fake" in out and f"{J}" in out and "fresh" in out
    shown = invoke("graph", "show")
    assert f"fresh    derive/{J}  (4 inputs)" in shown
    assert "fresh    tabulate/fake/" in shown
    assert "fresh    reference/jurisdictions" in shown


def test_the_catalog_lists_every_view(three):
    views = query("SELECT view_name FROM duckdb_views() WHERE NOT internal ORDER BY 1")
    names = [v["view_name"] for v in views]
    assert names == sorted(
        [
            "jurisdictions",
            "law_ancestors",
            "law_attachments",
            "law_codes",
            "law_history",
            "law_in_force",
            "law_nodes",
            "law_observations",
            "law_subdivisions",
            "law_versions",
            "publications",
            "segment_texts",
            "segments",
            "sessions",
        ]
    )
    [check] = query(
        "SELECT count(*) AS n, count(*) FILTER (WHERE sha256(content) <> content_sha256) AS bad"
        " FROM segment_texts"
    )
    assert check["n"] > 0 and check["bad"] == 0
    out = CliRunner().invoke(cli.app(fake.REGISTRY, fake.APP), ["sql", "SELECT * FROM nowhere"])
    assert out.exit_code == 1 and "nowhere" in out.output


def test_a_section_sits_where_it_sat_on_the_date(tmp_path):
    publish(tmp_path, P1, "p1.txt")
    moved = P1.replace("publication 2024-01-15", "publication 2024-09-01").replace(
        "node 2 | Chapter 2 | Animals", "node 3 | Chapter 3 | Pets"
    )
    publish(tmp_path, moved, "p2.txt")
    invoke("crunch")
    invoke("derive")
    assert law_at(date(2024, 3, 1))["2-1"]["node_treepath"] == "2"
    assert law_at(date(2024, 10, 1))["2-1"]["node_treepath"] == "3"
    # Before its first publication, a version has its earliest placement.
    assert law_at(date(2023, 1, 1))["2-1"]["node_id"] == f"{J}/node/municipal/2"


def test_the_catalog_follows_a_moved_data_root(cfg, three, monkeypatch):
    moved = cfg.data_root.parent / "moved"
    cfg.data_root.rename(moved)
    monkeypatch.setenv("NOMOSCOPE_DATA_ROOT", str(moved))
    assert sorted(law_at(date(2025, 2, 1))) == ["1-1", "1-2"]


def test_cite_parse():
    sections = ["67-14", "67-14A", "67-3"]
    assert cite.parse("§ 67-14(A)(1)", sections) == ("67-14", "A/1")
    assert cite.parse("67-14 A(1)", sections) == ("67-14", "A/1")
    assert cite.parse("67-14A(1)", sections) == ("67-14A", "1")
    assert cite.parse("67-3/public-place", sections) == ("67-3", "public-place")
    assert cite.parse("67-14", sections) == ("67-14", "")
    assert cite.parse("99-1", sections) is None


def test_cite_gives_the_subdivision_in_force(three, cfg):
    before = cite.lookup(cfg, J, "municipal", "1-2(a)", date(2024, 3, 1))
    after = cite.lookup(cfg, J, "municipal", "1-2(a)", date(2024, 7, 1))
    assert before is not None and after is not None
    assert before.text == '(a) "City" means Windytown.'
    assert after.text == '(a) "City" means the City of Windytown.'
    assert before.law_version_id != after.law_version_id
    assert cite.lookup(cfg, J, "municipal", "1-2(c)", date(2024, 3, 1)) is None
    whole = cite.lookup(cfg, J, "municipal", "1-2", date(2024, 3, 1))
    assert whole is not None and whole.text.startswith("(a)") and whole.path == ""


def test_cite_command(three):
    out = invoke("cite", J, "municipal", "1-2(b)", "--on", "2024-07-01")
    assert out.splitlines()[:2] == [
        "1-2(b)  Definitions  [subsection]",
        '(b) "Code" means this code.',
    ]
    assert "in force" in out
