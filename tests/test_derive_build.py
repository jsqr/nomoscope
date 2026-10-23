from datetime import date

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from nomoscope.crunch import routes
from nomoscope.derive import build, registry
from nomoscope.graph import steps
from nomoscope.graph.steps import DERIVE, StepKey
from nomoscope.graph.store import Store
from nomoscope.partition import dataset_path, reference_path
from tests.pipeline import P1, P2, P3, crunch, derive, publish

pytestmark = pytest.mark.usefixtures("conventions")
J = "us-il-windytown"


def read(cfg, dataset, j=J):
    return pl.read_parquet(dataset_path(cfg, dataset, j))


def by(frame, column):
    return {r[column]: r for r in frame.to_dicts()}


def three(cfg, tmp_path):
    publish(cfg, tmp_path, P1, "p1.txt")
    publish(cfg, tmp_path, P2, "p2.txt", completeness="partial")
    publish(cfg, tmp_path, P3, "p3.txt")
    crunch(cfg)


def test_a_section_is_its_versions_with_their_dates(cfg, tmp_path):
    three(cfg, tmp_path)
    [result] = derive(cfg)
    assert (result.jurisdiction, result.mode, result.publications) == (J, "refold", 3)
    versions = read(cfg, registry.LAW_VERSIONS)
    sections = versions.group_by("section_id").len().sort("section_id")
    assert sections.rows() == [
        (f"{J}/law/municipal/1-1", 1),
        (f"{J}/law/municipal/1-2", 2),
        (f"{J}/law/municipal/2-1", 1),
    ]
    old, new = versions.filter(pl.col("section_num") == "1-2").sort("first_seen").to_dicts()
    # The partial publication amended it, and the next full one kept the change.
    assert (old["first_seen"], old["last_seen"], old["absent_from"]) == (
        date(2024, 1, 15),
        date(2024, 1, 15),
        date(2025, 1, 10),
    )
    assert old["successor_id"] == new["id"]
    assert (new["first_seen"], new["last_seen"], new["absent_from"]) == (
        date(2024, 6, 1),
        date(2025, 1, 10),
        None,
    )
    assert (new["enacted_date"], new["effective_date"]) == (
        date(2024, 5, 1),
        date(2024, 6, 1),
    )
    assert new["observations"] == 2 and new["first_publication"] != new["last_publication"]
    assert new["id"].startswith(f"{J}/law/municipal/1-2/") and len(new["id"].split("/")[-1]) == 12
    [dropped] = versions.filter(pl.col("section_num") == "2-1").to_dicts()
    assert (dropped["last_seen"], dropped["absent_from"]) == (
        date(2024, 1, 15),
        date(2025, 1, 10),
    )
    assert dropped["successor_id"] is None


def test_the_other_datasets(cfg, tmp_path):
    three(cfg, tmp_path)
    derive(cfg)
    pubs = read(cfg, registry.PUBLICATIONS)
    assert pubs.select("edition", "published_basis", "completeness", "sections").rows() == [
        ("Supp. No. 1", "stated", "full", 3),
        ("Supp. No. 2", "stated", "partial", 1),
        ("Supp. No. 3", "stated", "full", 2),
    ]
    assert set(pubs["source"]) == {"fake"}
    nodes = by(read(cfg, registry.LAW_NODES), "id")
    assert nodes[f"{J}/node/municipal/2"]["absent_from"] == date(2025, 1, 10)
    assert nodes[f"{J}/node/municipal/1"]["absent_from"] is None
    ancestors = read(cfg, registry.LAW_ANCESTORS)
    edge = ancestors.filter(pl.col("descendant_id") == f"{J}/law/municipal/2-1").to_dicts()
    assert [(e["ancestor_id"], e["depth"], e["absent_from"]) for e in edge] == [
        (f"{J}/node/municipal/2", 1, date(2025, 1, 10))
    ]
    subdivisions = read(cfg, registry.LAW_SUBDIVISIONS)
    amended = subdivisions.filter(pl.col("section_id") == f"{J}/law/municipal/1-2")
    assert sorted(set(amended["level"])) == ["subsection"]
    assert amended.height == 5  # (a) (b) in one version, (a) (b) (c) in the other
    segments = read(cfg, registry.SEGMENTS)
    [title] = segments.filter(pl.col("block_id").str.contains("/1-1/")).to_dicts()
    assert title["context"] == "General Provisions > municipal 1-1 Title"
    assert read(cfg, registry.SESSIONS).height == 0
    jurisdictions = pl.read_parquet(reference_path(cfg, registry.JURISDICTIONS))
    assert jurisdictions.filter(pl.col("id") == "us-il")["state_fips"].to_list() == ["17"]


def test_a_rerun_is_a_no_op_and_a_new_publication_folds_onto_the_stored(cfg, tmp_path):
    publish(cfg, tmp_path, P1, "p1.txt")
    publish(cfg, tmp_path, P2, "p2.txt", completeness="partial")
    crunch(cfg)
    [first] = derive(cfg)
    assert first.mode == "refold"
    assert derive(cfg) == []
    publish(cfg, tmp_path, P3, "p3.txt")
    crunch(cfg)
    [second] = derive(cfg)
    assert (second.mode, second.publications) == ("fold", 1)
    folded = {d: read(cfg, d) for d in registry.DATASETS}
    with Store(cfg).open(read_only=True) as conn:
        [inputs] = [steps.inputs_of(conn, StepKey(DERIVE, J, ""))]
    assert [i.stage for i in inputs] == ["carve", "carve", "carve", "config"]
    [again] = derive(cfg, force=True)
    assert again.mode == "refold"
    for dataset, frame in folded.items():
        assert_frame_equal(frame, read(cfg, dataset), check_row_order=False)


def test_a_changed_publication_refolds(cfg, tmp_path):
    three(cfg, tmp_path)
    derive(cfg)
    store = Store(cfg)
    with store.open(read_only=True) as conn:
        [plan] = build.plan(conn, routes.load(cfg))
    changed = plan.planned.inputs[0]
    stale = [steps.Input(changed.stage, changed.scope, changed.unit, "0" * 64)]
    assert build.added(plan.planned, steps.Staleness.INPUT, stale) is None
    assert build.added(plan.planned, steps.Staleness.CODE, list(plan.planned.inputs)) is None


def test_jurisdictions_build_apart(cfg, tmp_path):
    publish(cfg, tmp_path, P1, "a.txt")
    publish(cfg, tmp_path, P3, "b.txt", jurisdiction="us-il-elgin")
    crunch(cfg)
    results = derive(cfg)
    assert sorted(r.jurisdiction for r in results) == ["us-il-elgin", J]
    elgin = read(cfg, registry.LAW_VERSIONS, "us-il-elgin")
    assert set(elgin["jurisdiction"]) == {"us-il-elgin"}
    assert elgin.height == 2 and read(cfg, registry.LAW_VERSIONS).height == 3
    assert (
        build.build(cfg, Store(cfg), ["us-il-elgin"], force=True)[0].jurisdiction == "us-il-elgin"
    )


def test_a_change_to_the_conventions_rebuilds(cfg, tmp_path):
    three(cfg, tmp_path)
    derive(cfg)
    other = cfg.config_root / "conventions" / "other.toml"
    other.write_text('[levels]\nwords = ["part"]\n')
    [result] = derive(cfg)
    assert result.mode == "refold"
    assert derive(cfg) == []


def test_a_changed_history_note_keeps_each_notes_dates(cfg, tmp_path):
    publish(cfg, tmp_path, P1, "p1.txt")
    noted = P1.replace("publication 2024-01-15", "publication 2024-04-01").replace(
        "section 1-1 | Title | Ord. 2019-12, passed 12-3-2019, eff. 1-1-2020",
        "section 1-1 | Title | Ord. 2019-12, passed 12-3-2019, eff. 1-1-2020;"
        " Ord. 2024-9, eff. 3-1-2024",
    )
    publish(cfg, tmp_path, noted, "p2.txt")
    crunch(cfg)
    derive(cfg)
    title = f"{J}/law/municipal/1-1"
    [version] = read(cfg, registry.LAW_VERSIONS).filter(pl.col("section_id") == title).to_dicts()
    assert version["effective_date"] == date(2024, 3, 1)
    notes = read(cfg, registry.LAW_HISTORY).filter(pl.col("section_id") == title)
    assert notes.select("law_version_id", "effective_date", "first_seen", "last_seen").rows() == [
        (version["id"], date(2020, 1, 1), date(2024, 1, 15), date(2024, 1, 15)),
        (version["id"], date(2024, 3, 1), date(2024, 4, 1), date(2024, 4, 1)),
    ]
    assert all(i.startswith(version["id"] + "/history/") for i in notes["id"])


def test_text_printed_differently_is_one_version(cfg, tmp_path):
    publish(cfg, tmp_path, P1, "p1.txt")
    respaced = P1.replace("publication 2024-01-15", "publication 2024-04-01").replace(
        '(a) "City" means Windytown.', "(a)  “City”\tmeans Windytown.  "
    )
    publish(cfg, tmp_path, respaced, "p2.txt")
    crunch(cfg)
    derive(cfg)
    versions = read(cfg, registry.LAW_VERSIONS).filter(pl.col("section_num") == "1-2")
    assert versions.height == 1
    assert versions["text"][0].startswith('(a) "City" means Windytown.\n')
