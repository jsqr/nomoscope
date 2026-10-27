"""Build the catalog: catalog.duckdb, a file of views over the derived datasets.

There is one view per dataset, one per reviews table, one of the coverage
rows in force, plus the views and macros in schema.sql.
The catalog holds no data, so it is rebuilt whole after each derive.

The views read parquet files by paths relative to the data root. connect
sets DuckDB's file_search_path to the data root, so the data root can
move. A plain duckdb.connect must set file_search_path itself.
"""

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import duckdb

from nomoscope import coverage
from nomoscope.crunch import reviews
from nomoscope.derive import registry
from nomoscope.partition import DATA_FILE
from nomoscope.settings import Settings

# schema.sql is applied only when these views exist.
NEEDS = (
    registry.LAW_VERSIONS,
    registry.LAW_OBSERVATIONS,
    registry.LAW_SUBDIVISIONS,
    registry.SESSIONS,
    registry.PUBLICATIONS,
)


def schema() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


@dataclass(frozen=True)
class CatalogResult:
    path: Path
    views: tuple[str, ...]

    @property
    def summary(self) -> str:
        return f"catalog  {len(self.views)} views  {self.path}"


def dataset_glob(cfg: Settings, dataset: str) -> str | None:
    """The glob over a dataset's files, relative to the data root. None if it has no files."""
    root = cfg.derived / dataset
    relative = root.relative_to(cfg.data_root)
    if (root / DATA_FILE).is_file():
        return (relative / DATA_FILE).as_posix()
    if any(root.glob(f"*/{DATA_FILE}")):
        return (relative / "*" / DATA_FILE).as_posix()
    return None


def search_path(conn: duckdb.DuckDBPyConnection, cfg: Settings) -> None:
    quoted = str(cfg.data_root).replace("'", "''")
    conn.execute(f"SET file_search_path = '{quoted}'")


def build(cfg: Settings) -> CatalogResult:
    tmp = cfg.catalog.with_suffix(".duckdb.tmp")
    tmp.unlink(missing_ok=True)
    views = []
    conn = duckdb.connect(str(tmp))
    try:
        search_path(conn, cfg)
        for dataset in (*registry.REFERENCE_DATASETS, *registry.DATASETS):
            glob = dataset_glob(cfg, dataset)
            if glob is None:
                continue
            quoted = glob.replace("'", "''")
            conn.execute(
                f"CREATE VIEW {dataset} AS SELECT * FROM read_parquet("
                f"'{quoted}', hive_partitioning = false, union_by_name = true)"
            )
            views.append(dataset)
        for table in reviews.TABLES:
            glob = reviews.table_glob(cfg, table)
            if glob is None:
                continue
            quoted = glob.replace("'", "''")
            conn.execute(f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{quoted}')")
            views.append(table)
        glob = coverage.table_glob(cfg)
        if glob is not None:
            quoted = glob.replace("'", "''")
            conn.execute(
                f"CREATE VIEW {coverage.COVERAGE} AS SELECT * EXCLUDE (listed) FROM ("
                f"SELECT * FROM read_parquet('{quoted}') QUALIFY row_number() OVER "
                "(PARTITION BY jurisdiction ORDER BY written_at DESC) = 1) WHERE listed"
            )
            views.append(coverage.COVERAGE)
        if set(NEEDS) <= set(views):
            conn.execute(schema())
            views.append("law_in_force")
    finally:
        conn.close()
    os.replace(tmp, cfg.catalog)
    return CatalogResult(cfg.catalog, tuple(views))


def connect(cfg: Settings) -> duckdb.DuckDBPyConnection:
    """Open the catalog read-only, with file_search_path set to the data root."""
    conn = duckdb.connect(str(cfg.catalog), read_only=True)
    search_path(conn, cfg)
    return conn
