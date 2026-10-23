from pathlib import Path

import pytest

from nomoscope import partition
from nomoscope.settings import Settings


@pytest.mark.parametrize(
    "ok", ["us", "us-il", "us-il-chicago", "us-ca-los_angeles_county", "ca-on", "eu"]
)
def test_jurisdictions_from_the_doc(ok):
    assert partition.check_jurisdiction(ok) == ok


@pytest.mark.parametrize("bad", ["US", "us_ca", "u", "us-", "us-CA", "", "us ca", "usa-x."])
def test_bad_jurisdictions(bad):
    with pytest.raises(partition.PartitionError, match="jurisdiction"):
        partition.check_jurisdiction(bad)


def test_partition_path_orders_keys_and_validates():
    got = partition.partition_path(Path("/p"), {"jurisdiction": "us-il", "kind": "law_2025"})
    assert got == Path("/p/jurisdiction=us-il/kind=law_2025")
    with pytest.raises(partition.PartitionError, match="value"):
        partition.partition_path(Path("/p"), {"kind": "Law"})
    with pytest.raises(partition.PartitionError, match="key"):
        partition.partition_path(Path("/p"), {"Kind": "law"})
    with pytest.raises(partition.PartitionError, match="jurisdiction"):
        partition.partition_path(Path("/p"), {"jurisdiction": "usa"})


def test_a_publication_is_a_record_id():
    record = "2026-09-06T161743Z-830a8445"
    got = partition.partition_path(Path("/p"), {"publication": record})
    assert got == Path(f"/p/publication={record}")
    with pytest.raises(partition.PartitionError, match="record id"):
        partition.partition_path(Path("/p"), {"publication": "../x"})


def test_a_dataset_is_partitioned_by_jurisdiction_alone(tmp_path):
    cfg = Settings(data_root=tmp_path)
    got = partition.dataset_path(cfg, "law_versions", "us-il-chicago")
    assert got == tmp_path / "derived/law_versions/jurisdiction=us-il-chicago/data.parquet"
