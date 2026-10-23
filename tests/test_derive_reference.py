import polars as pl
import pytest

from nomoscope.derive import reference, registry
from nomoscope.derive.reference import ReferenceError
from nomoscope.graph.store import Store
from nomoscope.partition import reference_path

HEADER = "id,name,kind,parent,state_fips,county_fips,place_fips,geoid\n"


def test_the_projects_jurisdictions_join_the_packages(cfg):
    (cfg.config_root / reference.FILE).write_text(
        HEADER + "us-il-elgin,Elgin,city,us-il,17,,23074,1723074\n"
    )
    store = Store(cfg)
    [result] = reference.build(cfg, store)
    table = pl.read_parquet(reference_path(cfg, registry.JURISDICTIONS))
    assert table.filter(pl.col("id") == "us-il-elgin")["parent"].to_list() == ["us-il"]
    assert result.rows == table.height and "us-il" in table["id"].to_list()
    assert reference.build(cfg, store) == []
    (cfg.config_root / reference.FILE).write_text(HEADER)
    assert len(reference.build(cfg, store)) == 1


def test_a_jurisdiction_twice_or_without_its_parent_is_refused(cfg):
    (cfg.config_root / reference.FILE).write_text(HEADER + "us-il,Illinois,state,us,17,,,17\n")
    with pytest.raises(ReferenceError, match="duplicate ids"):
        reference.build(cfg, Store(cfg))
    (cfg.config_root / reference.FILE).write_text(HEADER + "us-zz-x,X,city,us-zz,,,,\n")
    with pytest.raises(ReferenceError, match="parents not in the table"):
        reference.build(cfg, Store(cfg))
