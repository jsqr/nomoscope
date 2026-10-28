"""The tabulated tables: what every cruncher writes, and their schemas.

Each table has one partition per publication:

- law_sections: one row per section. drafting names the section's
  drafting conventions.
- law_nodes: one row per heading.
- law_publications: one row, describing the publication.
- sessions: optional, for jurisdictions with legislative sessions.

Every row has a jurisdiction column. conform fills missing optional
columns with nulls, casts every column, and rejects unknown or missing
required columns.

conform also normalizes section text (`nomoscope.text`) before hashing
it, so the same words printed with different spacing or quotes hash the
same. The source's original form belongs in markup.
"""

from dataclasses import dataclass
from datetime import date

import polars as pl

from nomoscope.crunch import CrunchError
from nomoscope.graph import fingerprint
from nomoscope.graph.records import RecordRow
from nomoscope.text import normalize, text_sha256

LAW_SECTIONS = "law_sections"
LAW_NODES = "law_nodes"
LAW_PUBLICATIONS = "law_publications"
SESSIONS = "sessions"
# The codes a publication holds, and the material in it that is not law.
LAW_CODES = "law_codes"
LAW_ATTACHMENTS = "law_attachments"
TABLES = (LAW_SECTIONS, LAW_NODES, LAW_PUBLICATIONS, SESSIONS, LAW_CODES, LAW_ATTACHMENTS)

BASES = ("stated", "last_modified", "fetched")
# A section's subdivisions, where the cruncher knows them: spans into its
# normalized text. path is below the section ("A/1"); level is the source's
# word for the level.
SUBDIVISIONS = pl.List(
    pl.Struct(
        {
            "path": pl.String,
            "level": pl.String,
            "style": pl.String,
            "number": pl.String,
            "start_char": pl.Int64,
            "end_char": pl.Int64,
        }
    )
)
COMPLETENESS = ("full", "partial")


def tabulated(zone: str) -> dict[str, pl.Schema]:
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
                # The notes of the nearest division, for a section with none.
                "division_history": pl.String,
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
                "subdivisions": SUBDIVISIONS,
                "drafting": pl.String,
                # The law_code of the part this section holds, as a water supply
                # code incorporated in a section.
                "part_code": pl.String,
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
                "part_code": pl.String,
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
        # One row per code in a publication: the main code, then its parts.
        # kind is law or rules (in the law tables), or reference, exhibit,
        # apparatus, or superseded (in law_attachments). A part within a division names
        # the host code and the division's treepath.
        LAW_CODES: pl.Schema(
            {
                "jurisdiction": pl.String,
                "law_code": pl.String,
                "kind": pl.String,
                "title": pl.String,
                "host_law_code": pl.String,
                "host_treepath": pl.String,
                "page_first": pl.Int64,
                "page_last": pl.Int64,
            }
        ),
        # The text of each part that is not law: reproduced law, an exhibit,
        # or the publisher's apparatus.
        LAW_ATTACHMENTS: pl.Schema(
            {
                "jurisdiction": pl.String,
                "code": pl.String,
                "kind": pl.String,
                "title": pl.String,
                "host_law_code": pl.String,
                "host_treepath": pl.String,
                "page_first": pl.Int64,
                "page_last": pl.Int64,
                "text": pl.String,
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
    LAW_CODES: ("jurisdiction", "law_code", "kind"),
    LAW_ATTACHMENTS: ("jurisdiction", "code", "kind", "text"),
}


# Bump when conform gives different output for the same frame, so every
# tabulate reruns.
VERSION = "4"


def conform(table: str, frame: pl.DataFrame, zone: str = "UTC") -> pl.DataFrame:
    """Return frame in the schema of table.

    Normalizes section text, and computes text_sha256 if the frame has none.
    """
    schema = tabulated(zone)[table]
    extra = set(frame.columns) - set(schema.names())
    if extra:
        raise CrunchError(f"{table}: columns not in the tabulated schema: {sorted(extra)}")
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


def tabulate_version(version: str, zone: str = "UTC") -> str:
    """A tabulate step's code version: its cruncher's, the tabulated schemas, and conform's."""
    return fingerprint.code_version(f"{version}-{VERSION}", *tabulated(zone).values())
