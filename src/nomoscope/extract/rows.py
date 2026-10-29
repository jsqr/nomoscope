"""The derived law datasets of one jurisdiction, loaded for extraction.

`nodes` keeps only the latest wording of each heading, as segment contexts do.
"""

from dataclasses import dataclass

import polars as pl

from nomoscope.derive import registry
from nomoscope.extract.names import NONE, Names
from nomoscope.partition import dataset_path, reference_path
from nomoscope.settings import Settings


@dataclass(frozen=True)
class Rows:
    jurisdiction: str
    name: str | None
    versions: pl.DataFrame
    nodes: pl.DataFrame
    subdivisions: pl.DataFrame
    history: pl.DataFrame
    # None when the crunchers wrote no notes.
    notes: pl.DataFrame | None = None
    names: Names = NONE


def latest_nodes(nodes: pl.DataFrame) -> pl.DataFrame:
    return nodes.sort("last_seen", "last_publication").unique("id", keep="last")


def load(cfg: Settings, jurisdiction: str) -> Rows | None:
    paths = {
        d: dataset_path(cfg, d, jurisdiction)
        for d in (
            registry.LAW_VERSIONS,
            registry.LAW_NODES,
            registry.LAW_SUBDIVISIONS,
            registry.LAW_HISTORY,
            registry.LAW_NOTES,
        )
    }
    if not paths[registry.LAW_VERSIONS].is_file():
        return None
    notes = paths[registry.LAW_NOTES]
    return Rows(
        jurisdiction,
        name_of(cfg, jurisdiction),
        pl.read_parquet(paths[registry.LAW_VERSIONS]),
        latest_nodes(pl.read_parquet(paths[registry.LAW_NODES])),
        pl.read_parquet(paths[registry.LAW_SUBDIVISIONS]),
        pl.read_parquet(paths[registry.LAW_HISTORY]),
        pl.read_parquet(notes) if notes.is_file() else None,
    )


def name_of(cfg: Settings, jurisdiction: str) -> str | None:
    path = reference_path(cfg, registry.JURISDICTIONS)
    if not path.is_file():
        return None
    found = pl.read_parquet(path).filter(pl.col("id") == jurisdiction)
    return found["name"][0] if found.height else None
