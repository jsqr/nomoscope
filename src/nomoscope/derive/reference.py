"""The reference datasets, built from tables in the package and the config root.

jurisdictions lists each jurisdiction id with its name, kind, parent, and
Census codes. The package holds the country, the states, and DC. A
project adds its own in jurisdictions.csv in the config root.
"""

import hashlib
from dataclasses import dataclass
from importlib import resources

import polars as pl

from nomoscope.derive import registry
from nomoscope.errors import NomoscopeError
from nomoscope.graph import fingerprint, steps
from nomoscope.graph.steps import REFERENCE, Input, Planned, Staleness, StepKey
from nomoscope.graph.store import Store
from nomoscope.parquet import write_parquet
from nomoscope.partition import check_jurisdiction, reference_path
from nomoscope.settings import Settings

VERSION = "1"
FILE = "jurisdictions.csv"
SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "name": pl.String,
        "kind": pl.String,
        "parent": pl.String,
        "state_fips": pl.String,
        "county_fips": pl.String,
        "place_fips": pl.String,
        "geoid": pl.String,
    }
)


class ReferenceError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Table:
    name: str
    data: bytes

    @property
    def input(self) -> Input:
        stage, scope = ("package", "nomoscope.derive") if self.name == FILE else ("config", "")
        return Input(stage, scope, self.name, hashlib.sha256(self.data).hexdigest())


def tables(cfg: Settings) -> list[Table]:
    """The package's table, then the project's if it has one."""
    found = [Table(FILE, resources.files(__package__).joinpath(FILE).read_bytes())]
    if cfg.config_root is not None and (cfg.config_root / FILE).is_file():
        found.append(Table(str(cfg.config_root / FILE), (cfg.config_root / FILE).read_bytes()))
    return found


def jurisdictions(*found: Table) -> pl.DataFrame:
    """Concatenate the tables and check that ids are valid and unique and parents exist."""
    try:
        frame = pl.concat([pl.read_csv(t.data, schema=SCHEMA) for t in found])
    except pl.exceptions.PolarsError as e:
        raise ReferenceError(f"{', '.join(t.name for t in found)}: {e}") from e
    for value in frame["id"]:
        check_jurisdiction(value)
    duplicated = frame.filter(pl.col("id").is_duplicated())["id"].unique().to_list()
    if duplicated:
        raise ReferenceError(f"{FILE}: duplicate ids {sorted(duplicated)}")
    orphans = frame.filter(
        pl.col("parent").is_not_null() & ~pl.col("parent").is_in(frame["id"].implode())
    )["id"].to_list()
    if orphans:
        raise ReferenceError(f"{FILE}: parents not in the table for {sorted(orphans)}")
    return frame.sort("id")


@dataclass(frozen=True)
class ReferenceResult:
    dataset: str
    rows: int

    @property
    def summary(self) -> str:
        return f"{self.dataset:24} reference {self.rows:9} rows"


def plan(found: list[Table]) -> Planned:
    return Planned(
        StepKey(REFERENCE, registry.JURISDICTIONS, ""),
        tuple(t.input for t in found),
        fingerprint.code_version(VERSION, SCHEMA),
    )


def build(cfg: Settings, store: Store, *, force: bool = False) -> list[ReferenceResult]:
    found = tables(cfg)
    planned = plan(found)
    with store.open() as conn:
        state = steps.classify(cfg, conn, [planned])[planned.key]
        if state is Staleness.FRESH and not force:
            return []
        frame = jurisdictions(*found)
        path = reference_path(cfg, registry.JURISDICTIONS)
        write_parquet(frame, path)
        row = steps.table_row(
            cfg,
            registry.JURISDICTIONS,
            path,
            frame.schema,
            frame.height,
            sha256=fingerprint.file_sha256(path),
        )
        steps.record(conn, planned, [row])
    return [ReferenceResult(registry.JURISDICTIONS, frame.height)]
