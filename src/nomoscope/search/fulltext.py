"""A BM25 index of segment text, in search.duckdb beside the catalog.

The index holds every segment's embedded text (its headings, lead-ins,
and body), stemmed (Porter) with English stopwords removed, using DuckDB's
fts extension. The file is local and rebuilt whenever the segments
change: it records the hashes derive gave the segments tables, and
`ensure` rebuilds it when they differ.
"""

import hashlib
import json
import os
from pathlib import Path

import duckdb

from nomoscope.catalog import build as catalog
from nomoscope.derive import registry
from nomoscope.graph import steps
from nomoscope.graph.steps import DERIVE
from nomoscope.graph.store import Store
from nomoscope.settings import Settings

FILE = "search.duckdb"


def path(cfg: Settings) -> Path:
    return cfg.data_root / FILE


def stamp(cfg: Settings) -> str:
    """A hash of the segments tables' hashes, as derive recorded them."""
    with Store(cfg).open(read_only=True) as conn:
        found = steps.table_hashes(conn, DERIVE)
    prefix = f"derived/{registry.SEGMENTS}/"
    tables = sorted((p, h) for p, h in found.items() if p.startswith(prefix))
    return hashlib.sha256(json.dumps(tables).encode()).hexdigest()


def current(cfg: Settings, expected: str) -> bool:
    if not path(cfg).is_file():
        return False
    conn = duckdb.connect(str(path(cfg)), read_only=True)
    try:
        [(found,)] = conn.execute("SELECT stamp FROM meta").fetchall()
    except duckdb.Error:
        return False
    finally:
        conn.close()
    return found == expected


def ensure(cfg: Settings) -> Path:
    """The index, rebuilt first if the segments changed since it was built."""
    expected = stamp(cfg)
    if current(cfg, expected):
        return path(cfg)
    tmp = path(cfg).with_suffix(".duckdb.tmp")
    tmp.unlink(missing_ok=True)
    conn = duckdb.connect(str(tmp))
    try:
        conn.execute("SET enable_progress_bar = false")
        catalog.search_path(conn, cfg)
        quoted = str(cfg.catalog).replace("'", "''")
        conn.execute(f"ATTACH '{quoted}' AS cat (READ_ONLY)")
        conn.execute(
            "CREATE TABLE docs AS SELECT id AS segment_id, jurisdiction, content"
            f" FROM cat.{catalog.SEGMENT_TEXTS}"
        )
        conn.execute("DETACH cat")
        conn.execute("LOAD fts")
        conn.execute(
            "PRAGMA create_fts_index('docs', 'segment_id', 'content',"
            " stemmer = 'porter', stopwords = 'english')"
        )
        conn.execute("CREATE TABLE meta AS SELECT ? AS stamp", [expected])
    finally:
        conn.close()
    os.replace(tmp, path(cfg))
    return path(cfg)


def ranked(cfg: Settings, query: str, jurisdiction: str, depth: int) -> list[tuple[str, float]]:
    """Segment ids of the jurisdiction by BM25 score, best first."""
    conn = duckdb.connect(str(ensure(cfg)), read_only=True)
    try:
        conn.execute("LOAD fts")
        return [
            (sid, float(score))
            for sid, score in conn.execute(
                "SELECT segment_id, score FROM ("
                " SELECT segment_id, jurisdiction,"
                " fts_main_docs.match_bm25(segment_id, ?) AS score FROM docs)"
                " WHERE score IS NOT NULL AND jurisdiction = ?"
                " ORDER BY score DESC LIMIT ?",
                [query, jurisdiction, depth],
            ).fetchall()
        ]
    finally:
        conn.close()
