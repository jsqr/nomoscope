from dataclasses import replace
from datetime import UTC, datetime

import polars as pl
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


JURISDICTIONS = pl.DataFrame(
    {
        "id": [
            "us-ny-rochester_city",
            "us-ny-hunter_town",
            "us-ny-hunter_village",
            "us-hi-honolulu_county",
            "us-va-arlington_county",
        ],
        "name": ["Rochester", "Hunter", "Hunter", "Honolulu", "Arlington"],
        "kind": ["city", "town", "village", "county", "county"],
    }
)


def listing(state: str, name: str, publisher: str = "pub") -> coverage.Listing:
    return coverage.Listing(state, name, publisher, f"https://x/{name}", "r1")


def test_a_listed_name_gives_place_and_kind():
    assert coverage.readings("Town of  Hunter") == [("hunter", "town")]
    assert coverage.readings("City and County of Honolulu") == [("honolulu", "city and county")]
    assert coverage.readings("Arlington County") == [
        ("arlington", "county"),
        ("arlington county", None),
    ]
    assert coverage.readings("Austin, TX") == [("austin", None)]
    assert coverage.readings("Winston-Salem") == [("winston-salem", None)]


def test_a_trailing_kind_may_be_part_of_the_name():
    jurisdictions = pl.DataFrame(
        {"id": ["us-ok-oklahoma_city"], "name": ["Oklahoma City"], "kind": ["city"]}
    )
    found = coverage.match(
        [coverage.Listing("ok", "Oklahoma City", "pub", "https://x/okc", "r1", kind="city")],
        jurisdictions,
        ["us-ok-oklahoma_city"],
    )
    assert [j for j, _ in found.exact] == ["us-ok-oklahoma_city"]


def test_the_kind_tells_a_city_from_a_town_of_the_same_name():
    found = coverage.match(
        [listing("ny", "City of Rochester"), listing("ny", "Town of Rochester")],
        JURISDICTIONS,
        ["us-ny-rochester_city"],
    )
    assert [(j, x.name) for j, x in found.exact] == [("us-ny-rochester_city", "City of Rochester")]
    assert found.ambiguous == []


def test_a_kind_shared_by_name_fits_a_consolidated_government():
    found = coverage.match(
        [listing("hi", "City and County of Honolulu"), listing("va", "Arlington County")],
        JURISDICTIONS,
        ["us-hi-honolulu_county", "us-va-arlington_county"],
    )
    assert [j for j, _ in found.exact] == ["us-hi-honolulu_county", "us-va-arlington_county"]


def test_a_kind_given_apart_from_the_name_is_used():
    hunter = coverage.Listing("ny", "Hunter", "pub", "https://x/hunter", "r1", kind="village")
    found = coverage.match([hunter], JURISDICTIONS, ["us-ny-hunter_town", "us-ny-hunter_village"])
    assert [j for j, _ in found.exact] == ["us-ny-hunter_village"]


def test_a_listing_without_a_kind_that_fits_two_is_ambiguous():
    found = coverage.match(
        [listing("ny", "Hunter, NY")], JURISDICTIONS, ["us-ny-hunter_town", "us-ny-hunter_village"]
    )
    assert found.exact == []
    assert [j for j, _ in found.ambiguous] == ["us-ny-hunter_town", "us-ny-hunter_village"]


def test_two_publishers_for_one_jurisdiction_are_ambiguous():
    found = coverage.match(
        [listing("ny", "Town of Hunter", "a"), listing("ny", "Hunter, NY", "b")],
        JURISDICTIONS,
        ["us-ny-hunter_town"],
    )
    assert found.exact == []
    [(j, candidates)] = found.ambiguous
    assert j == "us-ny-hunter_town" and [c.publisher for c in candidates] == ["a", "b"]


def test_only_listed_jurisdictions_are_matched():
    found = coverage.match([listing("ny", "Town of Hunter")], JURISDICTIONS, [])
    assert found.exact == [] and found.ambiguous == []


@pytest.mark.usefixtures("jurisdictions")
def test_match_writes_exact_matches_and_leaves_publishers_set_by_hand(cfg, monkeypatch):
    listings = [
        coverage.Listing("il", "City of Elgin", "pub", "https://x/elgin", "r1"),
        coverage.Listing("il", "City of Chicago", "pub", "https://x/chicago", "r1"),
        coverage.Listing("ny", "Hunter", "pub", "https://x/hunter", "r1"),
    ]
    monkeypatch.setattr(fake, "REGISTRY", replace(fake.REGISTRY, listings=lambda cfg: listings))
    coverage.write(
        cfg,
        [
            coverage.Entry("us-il-elgin", note="on the list"),
            coverage.Entry("us-il-chicago", publisher="mine", url="https://mine"),
            coverage.Entry("us-ny-hunter_town"),
            coverage.Entry("us-ny-hunter_village"),
        ],
        "jj",
        at(1),
    )
    out = invoke("coverage", "match", "--dry-run")
    assert "us-ny-hunter_town" in out and "ambiguous" in out
    assert coverage.rows(cfg).height == 4
    invoke("coverage", "match")
    found = {r["jurisdiction"]: r for r in coverage.in_force(cfg).iter_rows(named=True)}
    assert found["us-il-elgin"]["url"] == "https://x/elgin"
    assert found["us-il-elgin"]["note"] == "on the list; matched 'City of Elgin' in r1"
    assert found["us-il-chicago"]["url"] == "https://mine"
    assert found["us-ny-hunter_town"]["url"] is None


def test_match_needs_the_project_to_give_listings(cfg):
    out = CliRunner().invoke(cli.app(fake.REGISTRY, fake.APP), ["coverage", "match"])
    assert out.exit_code == 1 and "gives no listings" in out.output
