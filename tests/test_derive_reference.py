import polars as pl
import pytest
from typer.testing import CliRunner

from nomoscope import cli
from nomoscope.crunch.registry import Registry
from nomoscope.derive import census, reference, registry
from nomoscope.derive.reference import ReferenceError
from nomoscope.graph.store import Store
from nomoscope.partition import reference_path
from nomoscope.snarf import census as census_source
from tests import census as fixture
from tests import fake
from tests.pipeline import P1, crunch, publish


def table(cfg) -> dict[str, dict]:
    frame = pl.read_parquet(reference_path(cfg, registry.JURISDICTIONS))
    return {r["id"]: r for r in frame.to_dicts()}


def test_without_census_records_nothing_is_built(cfg):
    assert reference.build(cfg, Store(cfg)) == []
    assert not reference_path(cfg, registry.JURISDICTIONS).exists()


def test_the_census_files_give_governments_with_ids(cfg, tmp_path):
    fixture.write(cfg, tmp_path / "census")
    store = Store(cfg)
    [result] = reference.build(cfg, store)
    found = table(cfg)
    assert result.rows == len(found)
    assert sorted(found) == [
        "us",
        "us-dc",
        "us-il",
        "us-il-chicago",
        "us-il-cook_county",
        "us-il-cook_county-milan_township",
        "us-il-dekalb_county",
        "us-il-dekalb_county-milan_township",
        "us-il-elgin",
        "us-il-windytown",
        "us-ny",
        "us-ny-amenia",
        "us-ny-greene_county",
        "us-ny-hunter_town",
        "us-ny-hunter_village",
        "us-pr",
    ]
    assert (found["us-dc"]["kind"], found["us-pr"]["kind"]) == ("district", "territory")
    amenia = found["us-ny-amenia"]
    assert (amenia["name"], amenia["kind"], amenia["parent"]) == ("Amenia", "town", "us-ny")
    assert (amenia["county_fips"], amenia["geoid"]) == ("027", "3602701693")
    chicago = found["us-il-chicago"]
    assert (chicago["place_fips"], chicago["county_fips"]) == ("14000", None)
    assert reference.build(cfg, store) == []


def test_a_registered_jurisdiction_missing_from_the_table_stops_the_build(cfg, tmp_path):
    fixture.write(cfg, tmp_path / "census")
    publish(cfg, tmp_path, P1, "a.txt", jurisdiction="us-il-springfield")
    crunch(cfg)
    with pytest.raises(ReferenceError, match="us-il-springfield"):
        reference.build(cfg, Store(cfg))


def test_a_feed_without_a_record_stops_the_build(cfg, tmp_path):
    fixture.write(cfg, tmp_path / "census", feeds=(census_source.STATES, census_source.COUNTIES))
    with pytest.raises(ReferenceError, match="no Census record of places, cousubs"):
        reference.build(cfg, Store(cfg))


def test_names_lose_their_kind():
    assert census.split_kind("Juneau city and borough", "place") == ("Juneau", "city and borough")
    assert census.split_kind("District of Columbia", "county") == ("District of Columbia", "county")
    assert census.slug("Cañon City") == "canon_city"


def test_jurisdictions_find_prints_ids(cfg, tmp_path):
    fixture.write(cfg, tmp_path / "census")
    app = cli.app(Registry(sources={census_source.ID: census_source.SOURCE}), fake.APP)
    out = CliRunner().invoke(app, ["jurisdictions", "find", "hunter", "--state", "ny"])
    assert out.exit_code == 0, out.output
    assert [line.split()[0] for line in out.output.splitlines()] == [
        "us-ny-hunter_town",
        "us-ny-hunter_village",
    ]
