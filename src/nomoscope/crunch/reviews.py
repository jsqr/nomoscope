"""Reviews: a person's acceptance of a record's parsed code, and the sections they accepted.

Each acceptance writes one file to each of two tables under
`$data_root/reviews/`:

- `reviews`: who accepted which record of a code, when, the finding
  counts they accepted, the grammar's hash, and a note.
- `reviewed_sections`: one row per carved section of that record: the
  division it is in, its number, heading, and history, and hashes of its
  text and of its subdivisions. A correction can change the subdivisions
  and not the text, so both are kept.

Files are only added, so the tables keep every acceptance. The latest
review of a code is the one in force. Crunch overwrites a record's tables
on each run, so these are the only copy of what was accepted, and like
the archive they cannot be rebuilt.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from nomoscope.settings import Settings
from nomoscope.structure import Row
from nomoscope.text import normalize, text_sha256

REVIEWS = "reviews"
SECTIONS = "reviewed_sections"
TABLES = (REVIEWS, SECTIONS)

REVIEWS_SCHEMA = pl.Schema(
    {
        "review_id": pl.String,
        "source": pl.String,
        "jurisdiction": pl.String,
        "law_code": pl.String,
        "record": pl.String,
        "reviewed_by": pl.String,
        "reviewed_at": pl.Datetime("us"),
        "findings": pl.List(pl.Struct({"check": pl.String, "count": pl.Int64})),
        "grammar_sha256": pl.String,
        "note": pl.String,
    }
)
# node_treepath is the division a section is in, so it repeats.
KEY = ("node_treepath", "section_num")
FIELDS = ("heading", "history", "text_sha256", "subdivisions_sha256")
SECTIONS_SCHEMA = pl.Schema({c: pl.String for c in ("review_id", *KEY, *FIELDS)})


@dataclass(frozen=True)
class Review:
    review_id: str
    source: str
    jurisdiction: str
    law_code: str
    record: str
    reviewed_by: str
    reviewed_at: datetime  # UTC, without a zone
    findings: dict[str, int]
    grammar_sha256: str
    note: str | None = None


def root(cfg: Settings) -> Path:
    return cfg.data_root / REVIEWS


def table_glob(cfg: Settings, table: str) -> str | None:
    """The glob over a table's files, relative to the data root. None if it has no files."""
    folder = root(cfg) / table
    if not any(folder.glob("*.parquet")):
        return None
    return (folder.relative_to(cfg.data_root) / "*.parquet").as_posix()


def summary(sections: list[Row]) -> pl.DataFrame:
    """One row per carved section, as `reviewed_sections` keeps it, without the review id."""
    rows = [
        {
            "node_treepath": s["node_treepath"],
            "section_num": s["section_num"],
            "heading": s["heading"] or None,
            "history": s["history"] or None,
            "text_sha256": text_sha256(normalize(s["text"])),
            "subdivisions_sha256": hashlib.sha256(
                json.dumps(s["subdivisions"] or [], sort_keys=True).encode()
            ).hexdigest(),
        }
        for s in sections
    ]
    return pl.DataFrame(rows, schema={c: pl.String for c in (*KEY, *FIELDS)})


def write(cfg: Settings, review: Review, sections: pl.DataFrame) -> None:
    folder = root(cfg)
    name = f"{review.review_id}.parquet"
    row = {
        **{f: getattr(review, f) for f in REVIEWS_SCHEMA.names() if f != "findings"},
        "findings": [{"check": c, "count": n} for c, n in sorted(review.findings.items())],
    }
    for table, frame in (
        (SECTIONS, sections.select(pl.lit(review.review_id).alias("review_id"), pl.all())),
        (REVIEWS, pl.DataFrame([row], schema=REVIEWS_SCHEMA)),
    ):
        (folder / table).mkdir(parents=True, exist_ok=True)
        frame.write_parquet(folder / table / name)


def review_id(at: datetime, record: str) -> str:
    """The acceptance time, in the compact form of record ids, then the record."""
    return f"{at.astimezone(UTC).strftime('%Y-%m-%dT%H%M%SZ')}-{record}"


def latest(cfg: Settings, source: str, jurisdiction: str, law_code: str) -> Review | None:
    """The review in force for a code: its latest."""
    files = sorted((root(cfg) / REVIEWS).glob("*.parquet"))
    if not files:
        return None
    found = (
        pl.read_parquet(files, schema=REVIEWS_SCHEMA)
        .filter(
            (pl.col("source") == source)
            & (pl.col("jurisdiction") == jurisdiction)
            & (pl.col("law_code") == law_code)
        )
        .sort("reviewed_at")
    )
    if found.is_empty():
        return None
    r = found.row(-1, named=True)
    counts = {f["check"]: f["count"] for f in r["findings"]}
    return Review(**{**r, "findings": counts})


def accepted_sections(cfg: Settings, review: Review) -> pl.DataFrame:
    path = root(cfg) / SECTIONS / f"{review.review_id}.parquet"
    return pl.read_parquet(path, schema=SECTIONS_SCHEMA).drop("review_id")


def _keyed(frame: pl.DataFrame) -> dict[tuple[str, ...], Row]:
    """Rows by division and number; a repeated number is counted, as 5-1 then 5-1~2."""
    out: dict[tuple[str, ...], Row] = {}
    for r in frame.to_dicts():
        base = tuple(r[k] or "" for k in KEY)
        key, n = base, 1
        while key in out:
            n += 1
            key = (*base, str(n))
        out[key] = r
    return out


def changes(accepted: pl.DataFrame, current: pl.DataFrame) -> list[Row]:
    """Sections added, removed, or changed since review, in the current order."""
    before, after = _keyed(accepted), _keyed(current)

    def change(r: Row, what: str) -> Row:
        return {
            "node_treepath": r["node_treepath"],
            "section_num": r["section_num"],
            "change": what,
        }

    out = []
    for key, r in after.items():
        if key not in before:
            out.append(change(r, "added"))
            continue
        differ = [f.removesuffix("_sha256") for f in FIELDS if r[f] != before[key][f]]
        if differ:
            out.append(change(r, ", ".join(differ)))
    out += [change(r, "removed") for key, r in before.items() if key not in after]
    return out
