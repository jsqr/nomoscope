"""The carved tables: what every cruncher writes, and their schemas.

Each table has one partition per publication:

- law_sections: one row per section. drafting names the section's
  drafting conventions.
- law_nodes: one row per heading.
- law_publications: one row, describing the publication.
- sessions: optional, for jurisdictions with legislative sessions.

Every row has a jurisdiction column. conform fills missing optional
columns with nulls, casts every column, and rejects unknown or missing
required columns.

conform also normalizes section text before hashing it. It converts line
endings, composes Unicode (NFC), replaces curly quotes and non-ASCII
spaces, removes soft hyphens, collapses runs of spaces and tabs, and
trims each line. Other characters, such as the section sign, stay. The
number of lines does not change. The same words printed with different
spacing or quotes then hash the same. The source's original form belongs
in markup.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date

import polars as pl

from nomoscope.crunch import CrunchError
from nomoscope.graph import fingerprint
from nomoscope.graph.records import RecordRow

LAW_SECTIONS = "law_sections"
LAW_NODES = "law_nodes"
LAW_PUBLICATIONS = "law_publications"
SESSIONS = "sessions"
TABLES = (LAW_SECTIONS, LAW_NODES, LAW_PUBLICATIONS, SESSIONS)

BASES = ("stated", "last_modified", "fetched")
COMPLETENESS = ("full", "partial")


def carved(zone: str) -> dict[str, pl.Schema]:
    when = pl.Datetime("us", zone)
    return {
        LAW_SECTIONS: pl.Schema(
            {
                "jurisdiction": pl.String,
                "law_code": pl.String,
                "section_num": pl.String,
                "version_id": pl.String,
                "source_id": pl.String,
                "node_treepath": pl.String,
                "heading": pl.String,
                "history": pl.String,
                "effective_date": pl.Date,
                "statutory_default_date": pl.Date,
                "statute_year": pl.Int64,
                "statute_session": pl.Int64,
                "statute_chapter": pl.Int64,
                "statute_section": pl.String,
                "markup": pl.String,
                "text": pl.String,
                "text_sha256": pl.String,
                "blob_sha256": pl.String,
                "source_updated": when,
                "line_indents": pl.List(pl.Int64),
                "drafting": pl.String,
            }
        ),
        LAW_NODES: pl.Schema(
            {
                "jurisdiction": pl.String,
                "law_code": pl.String,
                "node_treepath": pl.String,
                "parent_treepath": pl.String,
                "level": pl.String,
                "number": pl.String,
                "heading": pl.String,
                "depth": pl.Int64,
                "position": pl.Int64,
                "sequence": pl.Int64,
                "contains_sections": pl.Boolean,
            }
        ),
        LAW_PUBLICATIONS: pl.Schema(
            {
                "publication": pl.String,
                "jurisdiction": pl.String,
                "law_codes": pl.List(pl.String),
                "published_date": pl.Date,
                "published_basis": pl.String,
                "completeness": pl.String,
                "edition": pl.String,
                "currency_text": pl.String,
                "currency_date": pl.Date,
                "session": pl.String,
            }
        ),
        SESSIONS: pl.Schema(
            {
                "jurisdiction": pl.String,
                "session": pl.String,
                "kind": pl.String,
                "start_date": pl.Date,
                "end_date": pl.Date,
            }
        ),
    }


REQUIRED = {
    LAW_SECTIONS: ("jurisdiction", "law_code", "section_num", "text"),
    LAW_NODES: ("jurisdiction", "law_code", "node_treepath"),
    LAW_PUBLICATIONS: (
        "publication",
        "jurisdiction",
        "published_date",
        "published_basis",
        "completeness",
    ),
    SESSIONS: ("jurisdiction", "session"),
}


QUOTES = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u2032": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u2033": '"',
    }
)
SPACES_RE = re.compile(r"[^\S\n]+")
# An invisible hint where a word may break; never part of the word.
SOFT_HYPHEN = "\u00ad"
# Bump when conform gives different output for the same frame, so every
# carve reruns.
VERSION = "2"


def normalize(text: str | None) -> str | None:
    if text is None:
        return None
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace(SOFT_HYPHEN, "")
    text = unicodedata.normalize("NFC", text)
    lines = (SPACES_RE.sub(" ", line).strip() for line in text.translate(QUOTES).split("\n"))
    return "\n".join(lines)


def text_sha256(text: str | None) -> str | None:
    return None if text is None else hashlib.sha256(text.encode("utf-8")).hexdigest()


def conform(table: str, frame: pl.DataFrame, zone: str = "UTC") -> pl.DataFrame:
    """Return frame in the schema of table.

    Normalizes section text, and computes text_sha256 if the frame has none.
    """
    schema = carved(zone)[table]
    extra = set(frame.columns) - set(schema.names())
    if extra:
        raise CrunchError(f"{table}: columns not in the carved schema: {sorted(extra)}")
    missing = [c for c in REQUIRED[table] if c not in frame.columns]
    if missing:
        raise CrunchError(f"{table}: missing required columns {missing}")
    if table == LAW_SECTIONS:
        frame = frame.with_columns(
            pl.col("text").cast(pl.String).map_elements(normalize, return_dtype=pl.String)
        )
        if "text_sha256" not in frame.columns:
            frame = frame.with_columns(
                pl.col("text")
                .map_elements(text_sha256, return_dtype=pl.String)
                .alias("text_sha256")
            )
    filled = frame.with_columns(
        pl.lit(None, dtype=dtype).alias(name)
        for name, dtype in schema.items()
        if name not in frame.columns
    )
    conformed = filled.select(pl.col(name).cast(dtype) for name, dtype in schema.items())
    if table == LAW_PUBLICATIONS:
        check_publications(conformed)
    return conformed


def check_publications(frame: pl.DataFrame) -> None:
    if frame.height != 1:
        raise CrunchError(f"{LAW_PUBLICATIONS}: one row per publication, not {frame.height}")
    row = frame.row(0, named=True)
    if row["published_basis"] not in BASES:
        raise CrunchError(f"published_basis must be one of {BASES}: {row['published_basis']!r}")
    if row["completeness"] not in COMPLETENESS:
        raise CrunchError(f"completeness must be one of {COMPLETENESS}: {row['completeness']!r}")


@dataclass(frozen=True)
class Stated:
    """What a publication states about itself, where it does.

    edition is text such as "Supp. No. 47". currency_text is text such as
    "current through Ord. 2025-31", and currency_date is its date.
    """

    published_date: date | None = None
    edition: str | None = None
    currency_text: str | None = None
    currency_date: date | None = None


NOTHING_STATED = Stated()


def publication(
    record: RecordRow, law_codes: list[str], stated: Stated = NOTHING_STATED
) -> pl.DataFrame:
    """Return the law_publications row for a record.

    The date is the stated one, else the record's last-modified time, else
    its fetch time.
    """
    if stated.published_date is not None:
        on, basis = stated.published_date, "stated"
    else:
        on, basis = record.published
    return conform(
        LAW_PUBLICATIONS,
        pl.DataFrame(
            {
                "publication": [record.record],
                "jurisdiction": [record.jurisdiction],
                "law_codes": [sorted(law_codes)],
                "published_date": [on],
                "published_basis": [basis],
                "completeness": [record.completeness],
                "edition": [stated.edition],
                "currency_text": [stated.currency_text],
                "currency_date": [stated.currency_date],
                "session": [record.session],
            },
            schema_overrides={"law_codes": pl.List(pl.String)},
        ),
    )


def carve_version(version: str, zone: str = "UTC") -> str:
    """A carve step's code version: its cruncher's, the carved schemas, and conform's."""
    return fingerprint.code_version(f"{version}-{VERSION}", *carved(zone).values())
