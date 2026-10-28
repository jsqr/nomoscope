"""Paths a cruncher writes to, under crunched/<cruncher>/:

<table>/publication=<unit>/<fingerprint>.parquet            snapshot tables
tabulated/<table>/publication=<record>/<fingerprint>.parquet  tabulated tables
steps/<stage>/<unit>/<fingerprint>.json                      step manifests

<fingerprint> is the first 16 hex digits of the step's fingerprint. A step
rerun under other inputs or code writes new files beside the old ones, so
a file name always holds the same content.
"""

from dataclasses import dataclass
from pathlib import Path

from nomoscope.partition import check_record_id, partition_path
from nomoscope.settings import Settings
from nomoscope.snarf.archive import check_source_id

FINGERPRINT_CHARS = 16


def file_stem(fingerprint: str) -> str:
    return fingerprint[:FINGERPRINT_CHARS]


@dataclass(frozen=True)
class Layout:
    cruncher: str

    def __post_init__(self) -> None:
        check_source_id(self.cruncher)

    def root(self, cfg: Settings) -> Path:
        return cfg.crunched / self.cruncher

    def snapshot_table(self, cfg: Settings, table: str, unit: str, fingerprint: str) -> Path:
        folder = partition_path(self.root(cfg) / table, {"publication": unit})
        return folder / f"{file_stem(fingerprint)}.parquet"

    def tabulated_root(self, cfg: Settings) -> Path:
        return self.root(cfg) / "tabulated"

    def tabulated_table(self, cfg: Settings, table: str, unit: str, fingerprint: str) -> Path:
        folder = partition_path(self.tabulated_root(cfg) / table, {"publication": unit})
        return folder / f"{file_stem(fingerprint)}.parquet"

    def manifest(self, cfg: Settings, stage: str, unit: str, fingerprint: str) -> Path:
        folder = self.root(cfg) / "steps" / stage / check_record_id(unit)
        return folder / f"{file_stem(fingerprint)}.json"
