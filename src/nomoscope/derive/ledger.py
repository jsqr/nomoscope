"""The ledger: each distinct row observed, with the publications that carried it.

A sighting is one publication's rows, one per key, stamped with the
publication's date and id. fold combines sightings into one row per key
with these columns:

- first_seen and last_seen: the first and last publication dates;
- last_full_seen: the last full publication's date;
- observations: the number of publications;
- first_publication and last_publication: their ids;
- the payload columns, from the latest sighting.

Each column is a min, max, sum, or latest value. So publications can be
folded in any order, and folding new ones onto a stored ledger gives the
same result as folding all of them again.

absent_from is the date of the first full publication of the row's code
after the row was last seen. It is null while the latest full publication
of the code carries the row. Partial publications do not set it.
"""

from collections.abc import Iterable, Sequence
from datetime import date

import polars as pl

FULL = "full"
SEEN = pl.Schema(
    {
        "first_seen": pl.Date,
        "last_seen": pl.Date,
        "last_full_seen": pl.Date,
        "observations": pl.Int64,
        "first_publication": pl.String,
        "last_publication": pl.String,
    }
)
CENSUS_SCHEMA = pl.Schema({"law_code": pl.String, "census_date": pl.Date})
LATEST = ("last_seen", "last_publication")


def sightings(
    frame: pl.DataFrame,
    key: Sequence[str],
    payload: Sequence[str] = (),
    *,
    publication: str,
    on: date,
    completeness: str,
) -> pl.DataFrame:
    seen = pl.lit(on, dtype=pl.Date)
    pub = pl.lit(publication, dtype=pl.String)
    return (
        frame.select(*key, *payload)
        .unique(subset=list(key), keep="first", maintain_order=True)
        .with_columns(
            seen.alias("first_seen"),
            seen.alias("last_seen"),
            (seen if completeness == FULL else pl.lit(None, dtype=pl.Date)).alias("last_full_seen"),
            pl.lit(1, dtype=pl.Int64).alias("observations"),
            pub.alias("first_publication"),
            pub.alias("last_publication"),
        )
    )


def fold(
    frames: Iterable[pl.DataFrame], key: Sequence[str], payload: Sequence[str] = ()
) -> pl.DataFrame | None:
    """Combine ledger frames into one row per key. None if there are no rows."""
    found = [f.select(*key, *payload, *SEEN.names()) for f in frames if f.height]
    if not found:
        return None
    latest = [pl.col(c) for c in LATEST]
    return (
        pl.concat(found, how="vertical_relaxed")
        .group_by(list(key))
        .agg(
            pl.col("first_seen").min(),
            pl.col("last_seen").max(),
            pl.col("last_full_seen").max(),
            pl.col("observations").sum(),
            pl.col("first_publication").sort_by("first_seen", "first_publication").first(),
            pl.col("last_publication").sort_by(*latest).last(),
            *(pl.col(c).sort_by(*latest).last() for c in payload),
        )
        .select(*key, *payload, *SEEN.names())
        .sort(list(key), nulls_last=False)
    )


def censuses(publications: pl.DataFrame) -> pl.DataFrame:
    """(law_code, census_date) for each code of each full publication."""
    return (
        publications.filter(pl.col("completeness") == FULL)
        .select(
            pl.col("law_codes").alias("law_code"),
            pl.col("published_date").alias("census_date"),
        )
        .explode("law_code")
        .drop_nulls()
        .unique()
        .sort("census_date", "law_code")
        .cast(CENSUS_SCHEMA)
    )


def with_absent_from(frame: pl.DataFrame, census: pl.DataFrame) -> pl.DataFrame:
    marked = frame.with_row_index("_row")
    found = (
        marked.select("_row", "law_code", "last_seen")
        .sort("last_seen")
        .join_asof(
            census,
            left_on="last_seen",
            right_on="census_date",
            by="law_code",
            strategy="forward",
            allow_exact_matches=False,
            # Both sides are sorted by date, so also within each code.
            check_sortedness=False,
        )
        .select("_row", pl.col("census_date").alias("absent_from"))
    )
    return marked.join(found, on="_row", how="left").sort("_row").drop("_row")
