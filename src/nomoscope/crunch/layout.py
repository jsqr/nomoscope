"""Paths a cruncher writes to, under crunched/<cruncher>/:

<table>/publication=<unit>/data.parquet           snapshot tables
carved/<table>/publication=<record>/data.parquet  carved tables
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

    def carved_root(self, cfg: Settings) -> Path:
        return self.root(cfg) / "carved"

    def carved_table(self, cfg: Settings, table: str, record_id: str) -> Path:
        return partition_path(self.carved_root(cfg) / table, {"publication": record_id}) / DATA_FILE
