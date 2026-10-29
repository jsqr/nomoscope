"""Build the catalog: catalog.duckdb, a file of views over the derived datasets.

There is one view per dataset, one per table the project names (`Registry.views`),
one of the coverage rows in force, plus the views and macros in schema.sql.
`definitions_in_force` and `penalties_in_force` add each version's interval
in force to the extracted rows from its text.
`segment_texts` gives each segment's embedded text, rebuilt as
`embed.text` does; `segment_embeddings` gives the stored vectors of the
`[embeddings]` model, one per jurisdiction and hash.
The catalog holds no data, so it is rebuilt whole after each derive.

The views read parquet files by paths relative to the data root. connect
sets DuckDB's file_search_path to the data root, so the data root can
move. A plain duckdb.connect must set file_search_path itself.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import duckdb

from nomoscope import coverage
from nomoscope.derive import registry
from nomoscope.embed import models, store
from nomoscope.partition import DATA_FILE
from nomoscope.settings import Settings

SEGMENT_TEXTS = "segment_texts"
SEGMENT_EMBEDDINGS = "segment_embeddings"
# The body is the block's text from start_char to end_char (0-based, in
# characters, as in Python); the content is segment.content's.
SEGMENT_TEXTS_SQL = f"""
CREATE VIEW {SEGMENT_TEXTS} AS
SELECT s.*, substr(v.text, s.start_char + 1, s.end_char - s.start_char) AS body,
       CASE WHEN coalesce(s.context, '') = '' THEN body
            ELSE s.context || chr(10) || chr(10) || body END AS content
FROM segments s
JOIN law_versions v ON v.jurisdiction = s.jurisdiction AND v.id = s.block_id
WHERE s.block_kind = 'law_section'
"""

IN_FORCE = ("definitions", "penalties")
IN_FORCE_SQL = """
CREATE VIEW {name}_in_force AS
SELECT x.*, f.start_date, f.end_date, f.start_basis, f.end_basis
FROM {name} x
JOIN law_in_force f ON f.jurisdiction = x.jurisdiction AND f.id = x.block_id
WHERE x.source = 'text'
"""

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


DATASET_OPTIONS = ", hive_partitioning = false, union_by_name = true"


def parquet(glob: str, options: str = "") -> str:
    """A read_parquet call over a glob, quoted for SQL."""
    quoted = glob.replace("'", "''")
    return f"read_parquet('{quoted}'{options})"


def view_sql(name: str, glob: str, options: str = "") -> str:
    return f"CREATE VIEW {name} AS SELECT * FROM {parquet(glob, options)}"


def build(cfg: Settings, project: Mapping[str, str] | None = None) -> CatalogResult:
    """Rebuild the catalog.

    `project` maps more view names to parquet globs relative to the data root.
    """
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
            conn.execute(view_sql(dataset, glob, DATASET_OPTIONS))
            views.append(dataset)
        for view, glob in (project or {}).items():
            conn.execute(view_sql(view, glob))
            views.append(view)
        glob = coverage.table_glob(cfg)
        if glob is not None:
            conn.execute(
                f"CREATE VIEW {coverage.COVERAGE} AS SELECT * EXCLUDE (listed) FROM ("
                f"SELECT * FROM {parquet(glob)} QUALIFY row_number() OVER "
                "(PARTITION BY jurisdiction ORDER BY written_at DESC) = 1) WHERE listed"
            )
            views.append(coverage.COVERAGE)
        if set(NEEDS) <= set(views):
            conn.execute(schema())
            views.append("law_in_force")
            for name in IN_FORCE:
                if name in views:
                    conn.execute(IN_FORCE_SQL.format(name=name))
                    views.append(f"{name}_in_force")
        if {registry.SEGMENTS, registry.LAW_VERSIONS} <= set(views):
            conn.execute(SEGMENT_TEXTS_SQL)
            views.append(SEGMENT_TEXTS)
        glob = store.glob(cfg, models.configured(cfg.table("embeddings")))
        if glob is not None:
            source = parquet(glob, ", hive_partitioning = true, filename = true")
            conn.execute(
                f"CREATE VIEW {SEGMENT_EMBEDDINGS} AS SELECT * EXCLUDE (filename) FROM {source}"
                " QUALIFY row_number() OVER"
                " (PARTITION BY jurisdiction, content_sha256 ORDER BY filename) = 1"
            )
            views.append(SEGMENT_EMBEDDINGS)
    finally:
        conn.close()
    os.replace(tmp, cfg.catalog)
    return CatalogResult(cfg.catalog, tuple(views))


def connect(cfg: Settings) -> duckdb.DuckDBPyConnection:
    """Open the catalog read-only, with file_search_path set to the data root."""
    conn = duckdb.connect(str(cfg.catalog), read_only=True)
    search_path(conn, cfg)
    return conn
