"""Paths a cruncher writes to, under crunched/<cruncher>/:

<table>/publication=<unit>/data.parquet           snapshot tables
tabulated/<table>/publication=<record>/data.parquet  tabulated tables
"""

from dataclasses import dataclass
from pathlib import Path

from nomoscope.partition import DATA_FILE, partition_path
from nomoscope.settings import Settings
from nomoscope.snarf.archive import check_source_id


@dataclass(frozen=True)
class Layout:
    cruncher: str

    def __post_init__(self) -> None:
        check_source_id(self.cruncher)

    def root(self, cfg: Settings) -> Path:
        return cfg.crunched / self.cruncher

    def snapshot_table(self, cfg: Settings, table: str, record_id: str) -> Path:
        return partition_path(self.root(cfg) / table, {"publication": record_id}) / DATA_FILE

    def tabulated_root(self, cfg: Settings) -> Path:
        return self.root(cfg) / "tabulated"

    def tabulated_table(self, cfg: Settings, table: str, record_id: str) -> Path:
        return (
            partition_path(self.tabulated_root(cfg) / table, {"publication": record_id}) / DATA_FILE
        )
