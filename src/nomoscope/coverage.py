"""Coverage: the jurisdictions a project collects codes for, with each code's publisher and URL.

The table is under `$data_root/coverage/`. Each file holds the rows
written together, and files are only added. A row says whether a
jurisdiction is listed or removed, and gives its publisher and URL as
they were known then. The latest row of a jurisdiction is the one in
force, so the table keeps every change.

A publisher is a source id when the project has a source for it, and
otherwise a name.

A project may also give listings: the codes a publisher's library lists,
by state and name. `match` pairs them with listed jurisdictions by state,
name, and kind.
"""

import re
from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

from nomoscope.errors import NomoscopeError
from nomoscope.parquet import write_parquet
from nomoscope.partition import check_jurisdiction
from nomoscope.settings import Settings

COVERAGE = "coverage"
SCHEMA = pl.Schema(
    {
        "jurisdiction": pl.String,
        "listed": pl.Boolean,
        "publisher": pl.String,
        "url": pl.String,
        "written_by": pl.String,
        "written_at": pl.Datetime("us"),
        "note": pl.String,
    }
)


class CoverageError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Entry:
    jurisdiction: str
    listed: bool = True
    publisher: str | None = None
    url: str | None = None
    note: str | None = None


def root(cfg: Settings) -> Path:
    return cfg.data_root / COVERAGE


def table_glob(cfg: Settings) -> str | None:
    """The glob over the table's files, relative to the data root. None if it has no files."""
    if not any(root(cfg).glob("*.parquet")):
        return None
    return (root(cfg).relative_to(cfg.data_root) / "*.parquet").as_posix()


def rows(cfg: Settings) -> pl.DataFrame:
    """Every row written."""
    files = sorted(root(cfg).glob("*.parquet"))
    if not files:
        return pl.DataFrame(schema=SCHEMA)
    return pl.read_parquet(files, schema=SCHEMA)


def latest(frame: pl.DataFrame) -> pl.DataFrame:
    """Each jurisdiction's latest row, listed or removed."""
    return (
        frame.sort("written_at", maintain_order=True)
        .unique("jurisdiction", keep="last")
        .sort("jurisdiction")
    )


def in_force(cfg: Settings) -> pl.DataFrame:
    """The listed jurisdictions, each with its latest row."""
    return latest(rows(cfg)).filter(pl.col("listed"))


def current(cfg: Settings, jurisdiction: str) -> Entry | None:
    """The jurisdiction's latest row, listed or removed. None if it has none."""
    found = latest(rows(cfg)).filter(pl.col("jurisdiction") == jurisdiction)
    if found.is_empty():
        return None
    r = found.row(0, named=True)
    return Entry(r["jurisdiction"], r["listed"], r["publisher"], r["url"], r["note"])


def change(
    cfg: Settings,
    jurisdiction: str,
    *,
    listed: bool = True,
    publisher: str | None = None,
    url: str | None = None,
    note: str | None = None,
) -> Entry:
    """The jurisdiction's next row: its latest, with the values given."""
    was = current(cfg, jurisdiction) or Entry(jurisdiction)
    return replace(
        was,
        listed=listed,
        publisher=publisher if publisher is not None else was.publisher,
        url=url if url is not None else was.url,
        note=note,
    )


def writer_slug(by: str) -> str:
    """The writer's name as it appears in a file name."""
    return re.sub(r"[^a-z0-9_-]+", "_", by.lower()).strip("_") or "unknown"


def write(cfg: Settings, entries: Sequence[Entry], by: str, at: datetime | None = None) -> Path:
    """Write the entries as one file, named by the time and the writer.

    Two people who write at the same moment write two files.
    """
    if not entries:
        raise CoverageError("nothing to write")
    for e in entries:
        check_jurisdiction(e.jurisdiction)
    repeated = [j for j, n in Counter(e.jurisdiction for e in entries).items() if n > 1]
    if repeated:
        raise CoverageError(f"more than one row for {', '.join(sorted(repeated))}")
    when = (at or datetime.now(UTC)).astimezone(UTC)
    path = root(cfg) / f"{when.strftime('%Y-%m-%dT%H%M%S%fZ')}-{writer_slug(by)}.parquet"
    if path.exists():
        raise CoverageError(f"{path} exists")
    frame = pl.DataFrame(
        [
            {
                "jurisdiction": e.jurisdiction,
                "listed": e.listed,
                "publisher": e.publisher,
                "url": e.url,
                "written_by": by,
                "written_at": when.replace(tzinfo=None),
                "note": e.note,
            }
            for e in entries
        ],
        schema=SCHEMA,
    )
    write_parquet(frame, path)
    return path


@dataclass(frozen=True)
class Listing:
    """A code a publisher's library lists."""

    state: str  # postal code, lowercase
    name: str  # as the library writes it: "City of Austin", "Austin, TX"
    publisher: str
    url: str
    record: str  # the archive record it was read from
    kind: str | None = None  # when the library gives it apart from the name


@dataclass(frozen=True)
class Matches:
    exact: list[tuple[str, Listing]]
    # A jurisdiction with several listings, or a listing that fits several jurisdictions.
    ambiguous: list[tuple[str, list[Listing]]]


KINDS = (
    "city and county",
    "city and borough",
    "consolidated government",
    "metropolitan government",
    "unified government",
    "charter township",
    "municipality",
    "township",
    "borough",
    "village",
    "county",
    "parish",
    "city",
    "town",
)
_KIND = "|".join(KINDS)
LEADING_KIND = re.compile(rf"^(?:the\s+)?({_KIND})\s+of\s+(.+)$", re.IGNORECASE)
TRAILING_KIND = re.compile(rf"^(.+?)\s+({_KIND})$", re.IGNORECASE)
STATE_SUFFIX = re.compile(r",\s*[A-Za-z. ]+$")


def readings(name: str) -> list[tuple[str, str | None]]:
    """A listed name's possible place names and kinds, lowercased.

    "Town of Hunter" is ("hunter", "town"). A trailing kind may be part of
    the name, so "Oklahoma City" is both ("oklahoma", "city") and
    ("oklahoma city", None).
    """
    text = STATE_SUFFIX.sub("", " ".join(name.split()))
    if m := LEADING_KIND.match(text):
        return [(m[2].lower(), m[1].lower())]
    if m := TRAILING_KIND.match(text):
        return [(m[1].lower(), m[2].lower()), (text.lower(), None)]
    return [(text.lower(), None)]


def kinds_agree(listed: str | None, census: str) -> bool:
    """A listing without a kind fits any; "city and county" fits a county or a city."""
    return listed is None or bool(set(listed.split()) & set(census.split()) - {"and"})


def match(
    listings: Iterable[Listing], jurisdictions: pl.DataFrame, listed: Iterable[str]
) -> Matches:
    """Pair listings with the listed jurisdictions of the same state, name, and kind.

    jurisdictions has the reference table's id, name, and kind columns.
    """
    wanted = set(listed)
    by_name: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for r in jurisdictions.filter(pl.col("id").is_in(wanted)).iter_rows(named=True):
        state = r["id"].split("-")[1]
        by_name[(state, r["name"].lower())].append((r["id"], r["kind"]))
    found: dict[str, list[Listing]] = defaultdict(list)
    fits: dict[Listing, list[str]] = {}
    for listing in listings:
        ids = list(
            dict.fromkeys(
                j
                for place, kind in readings(listing.name)
                for j, k in by_name.get((listing.state, place), [])
                if kinds_agree(listing.kind or kind, k)
            )
        )
        fits[listing] = ids
        for j in ids:
            found[j].append(listing)
    exact, ambiguous = [], []
    for j in sorted(found):
        candidates = found[j]
        if len(candidates) == 1 and len(fits[candidates[0]]) == 1:
            exact.append((j, candidates[0]))
        else:
            ambiguous.append((j, candidates))
    return Matches(exact, ambiguous)
