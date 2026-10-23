"""Registered records: what a source says about each archived record.

A registration names the record's jurisdiction, code, session, and
completeness. The code is empty when the record carries every code it
covers. The session is None when the source does not publish by session.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Protocol, overload

import duckdb

from nomoscope.partition import check_jurisdiction

RECORD = "record"
COLUMNS = (
    "source",
    "record",
    "feed",
    "jurisdiction",
    "code",
    "session",
    "completeness",
    "fetched_at",
    "last_modified",
    "sha256",
    "crunchable",
    "code_version",
    "registered_at",
    "item_count",
    "size",
)
COMPLETENESS = ("full", "partial")


@dataclass(frozen=True)
class Registration:
    record: str
    feed: str
    jurisdiction: str
    code: str
    session: str | None
    completeness: str
    fetched_at: datetime
    last_modified: datetime | None
    sha256: str
    crunchable: bool
    item_count: int | None
    size: int | None

    def __post_init__(self) -> None:
        check_jurisdiction(self.jurisdiction)
        if self.completeness not in COMPLETENESS:
            raise ValueError(f"completeness must be one of {COMPLETENESS}: {self.completeness!r}")


@dataclass(frozen=True)
class RecordRow(Registration):
    source: str
    code_version: str
    registered_at: datetime

    @property
    def source_time(self) -> datetime:
        """The source's last-modified time, else the fetch time."""
        return self.last_modified or self.fetched_at

    @property
    def published(self) -> tuple[date, str]:
        """The record's date, and whether last_modified or the fetch time gave it."""
        basis = "last_modified" if self.last_modified else "fetched"
        return self.source_time.date(), basis


class Identified(Protocol):
    @property
    def id(self) -> str: ...


class Fetched(Protocol):
    @property
    def feed(self) -> str: ...

    @property
    def fetched_at(self) -> datetime: ...

    @property
    def last_modified(self) -> str | None: ...

    @property
    def sha256(self) -> str: ...

    @property
    def item_count(self) -> int | None: ...

    @property
    def size(self) -> int | None: ...


def registration(
    record: Identified,
    fetch: Fetched,
    *,
    jurisdiction: str,
    completeness: str,
    crunchable: bool,
    code: str = "",
    session: str | None = None,
) -> Registration:
    return Registration(
        record=record.id,
        feed=fetch.feed,
        jurisdiction=jurisdiction,
        code=code,
        session=session,
        completeness=completeness,
        fetched_at=fetch.fetched_at,
        last_modified=http_date(fetch.last_modified),
        sha256=fetch.sha256,
        crunchable=crunchable,
        item_count=fetch.item_count,
        size=fetch.size,
    )


def http_date(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        return parsedate_to_datetime(text).astimezone(UTC)
    except TypeError, ValueError:
        return None


def to_db(when: datetime | None) -> datetime | None:
    return None if when is None else when.astimezone(UTC).replace(tzinfo=None)


@overload
def from_db(when: datetime) -> datetime: ...
@overload
def from_db(when: None) -> None: ...
def from_db(when: datetime | None) -> datetime | None:
    return None if when is None else when.replace(tzinfo=UTC)


def register(
    conn: duckdb.DuckDBPyConnection,
    source: str,
    rows: Iterable[Registration],
    code_version: str,
) -> None:
    now = to_db(datetime.now(UTC))
    values = [
        (
            source,
            r.record,
            r.feed,
            r.jurisdiction,
            r.code,
            r.session,
            r.completeness,
            to_db(r.fetched_at),
            to_db(r.last_modified),
            r.sha256,
            r.crunchable,
            code_version,
            now,
            r.item_count,
            r.size,
        )
        for r in rows
    ]
    if values:
        columns = ", ".join(COLUMNS)
        placeholders = ", ".join("?" for _ in COLUMNS)
        conn.executemany(
            f"INSERT OR REPLACE INTO records ({columns}) VALUES ({placeholders})",
            values,
        )


def registered(conn: duckdb.DuckDBPyConnection, source: str) -> dict[str, str]:
    """Map record id to the version it was registered under."""
    rows = conn.execute(
        "SELECT record, code_version FROM records WHERE source = ?", [source]
    ).fetchall()
    return dict(rows)


def records(
    conn: duckdb.DuckDBPyConnection,
    source: str | None = None,
    *,
    jurisdiction: str | None = None,
    crunchable: bool | None = None,
) -> list[RecordRow]:
    """Registered records, ordered by source, then record id."""
    sql = f"SELECT {', '.join(COLUMNS)} FROM records WHERE true"
    params: list[object] = []
    for column, value in (
        ("source", source),
        ("jurisdiction", jurisdiction),
        ("crunchable", crunchable),
    ):
        if value is not None:
            sql += f" AND {column} = ?"
            params.append(value)
    rows = conn.execute(sql + " ORDER BY source, record", params).fetchall()
    return [row_from(r) for r in rows]


def jurisdictions(conn: duckdb.DuckDBPyConnection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT jurisdiction FROM records WHERE crunchable ORDER BY jurisdiction"
    ).fetchall()
    return [r[0] for r in rows]


def row_from(values: tuple[Any, ...]) -> RecordRow:
    v = dict(zip(COLUMNS, values, strict=True))
    return RecordRow(
        source=v["source"],
        record=v["record"],
        feed=v["feed"],
        jurisdiction=v["jurisdiction"],
        code=v["code"],
        session=v["session"],
        completeness=v["completeness"],
        fetched_at=from_db(v["fetched_at"]),
        last_modified=from_db(v["last_modified"]),
        sha256=v["sha256"],
        crunchable=v["crunchable"],
        code_version=v["code_version"],
        registered_at=from_db(v["registered_at"]),
        item_count=v["item_count"],
        size=v["size"],
    )
