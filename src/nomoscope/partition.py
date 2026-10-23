"""Partition paths and ids for the crunched and derived tables.

Partition values contain only [a-z0-9_-]. Jurisdiction ids are ISO 3166-2
codes in lower case, extended with "-": us, us-il, us-il-chicago.
"""

import re
from collections.abc import Mapping
from pathlib import Path

from nomoscope.errors import NomoscopeError
from nomoscope.settings import Settings

DATA_FILE = "data.parquet"
VALUE_RE = re.compile(r"^[a-z0-9_-]+$")
KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
JURISDICTION_RE = re.compile(r"^[a-z]{2}(-[a-z0-9_]+)*$")
RECORD_ID_RE = re.compile(r"^[0-9A-Za-z_-]+$")


class PartitionError(NomoscopeError):
    pass


def check_value(value: str) -> str:
    if not VALUE_RE.match(value):
        raise PartitionError(f"partition value must match [a-z0-9_-]: {value!r}")
    return value


def check_key(key: str) -> str:
    if not KEY_RE.match(key):
        raise PartitionError(f"partition key must match [a-z][a-z0-9_]*: {key!r}")
    return key


def check_jurisdiction(value: str) -> str:
    if not JURISDICTION_RE.match(value):
        raise PartitionError(
            f"jurisdiction must be ISO 3166-2 lowercased, extended with '-': {value!r}"
        )
    return value


def partition_path(root: Path, keys: Mapping[str, str]) -> Path:
    """Return root/k1=v1/k2=v2 in the given key order.

    A publication value is a record id. It may contain capitals, so it is
    checked only for path safety.
    """
    path = root
    for key, value in keys.items():
        check_key(key)
        if key == "jurisdiction":
            check_jurisdiction(value)
        elif key == "publication":
            check_record_id(value)
        else:
            check_value(value)
        path = path / f"{key}={value}"
    return path


def check_record_id(value: str) -> str:
    if not RECORD_ID_RE.match(value):
        raise PartitionError(f"publication must be a snarf record id: {value!r}")
    return value


def dataset_path(cfg: Settings, dataset: str, jurisdiction: str) -> Path:
    return partition_path(cfg.derived / dataset, {"jurisdiction": jurisdiction}) / DATA_FILE


def reference_path(cfg: Settings, dataset: str) -> Path:
    return cfg.derived / check_key(dataset) / DATA_FILE
