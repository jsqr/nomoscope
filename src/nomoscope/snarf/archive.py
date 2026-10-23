"""Records in the archive: one directory per fetch or import.

A record directory is snarfed/<source>/<YYYY>/<timestamp>-<hash8>. It holds
fetch.json, which says where the bytes came from, and manifest.jsonl, with
one line per file: its name, blob hash, and size.
"""

import json
import os
import re
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from nomoscope.errors import NomoscopeError

SOURCE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
RESERVED_SOURCE_IDS = frozenset({"blobs"})
RECORD_DIR_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{6})Z-([0-9a-f]{8})(-\d+)?$")

FETCH_FILE = "fetch.json"
MANIFEST_FILE = "manifest.jsonl"


class ArchiveError(NomoscopeError):
    pass


def check_source_id(source: str) -> str:
    if not SOURCE_ID_RE.match(source) or source in RESERVED_SOURCE_IDS:
        raise ArchiveError(f"invalid source id: {source!r}")
    return source


def format_timestamp(ts: datetime) -> str:
    return ts.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


@dataclass(frozen=True)
class Fetch:
    source: str
    feed: str
    url: str
    params: dict[str, str]
    timestamp: str
    status: int
    etag: str | None
    last_modified: str | None
    sha256: str
    size: int
    item_count: int

    @property
    def fetched_at(self) -> datetime:
        return parse_timestamp(self.timestamp)


@dataclass(frozen=True)
class Item:
    name: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Record:
    path: Path

    @property
    def id(self) -> str:
        return self.path.name

    def fetch(self) -> Fetch:
        with (self.path / FETCH_FILE).open("rb") as f:
            return Fetch(**json.load(f))

    def items(self) -> Iterator[Item]:
        with (self.path / MANIFEST_FILE).open("rb") as f:
            for line in f:
                if line.strip():
                    yield Item(**json.loads(line))

    def item_map(self) -> dict[str, Item]:
        return {item.name: item for item in self.items()}


def record_dir(snarfed: Path, source: str, ts: datetime, sha256: str) -> Path:
    """Return the directory for a fetch, with a -N suffix if the name is taken.

    The name is taken when identical bytes were fetched in the same second.
    Both records are kept.
    """
    check_source_id(source)
    ts = ts.astimezone(UTC)
    name = f"{ts.strftime('%Y-%m-%dT%H%M%SZ')}-{sha256[:8]}"
    year = snarfed / source / ts.strftime("%Y")
    path = year / name
    n = 1
    while path.exists():
        n += 1
        path = year / f"{name}-{n}"
    return path


def write_record(path: Path, fetch: Fetch, items: list[Item]) -> Record:
    if path.exists():
        raise ArchiveError(f"record already exists: {path}")
    path.mkdir(parents=True)
    with (path / MANIFEST_FILE).open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(asdict(item)) + "\n")
    (path / FETCH_FILE).write_text(json.dumps(asdict(fetch), indent=2) + "\n", encoding="utf-8")
    return Record(path)


def records(snarfed: Path, source: str) -> list[Record]:
    """Every record of a source, oldest first.

    Records from the same second are ordered by directory mtime.
    """
    base = snarfed / check_source_id(source)
    if not base.is_dir():
        return []
    found = [
        d
        for year in base.iterdir()
        if year.is_dir()
        for d in year.iterdir()
        if RECORD_DIR_RE.match(d.name) and (d / FETCH_FILE).is_file()
    ]
    found.sort(key=lambda d: (d.name[:18], d.stat().st_mtime_ns, d.name))
    return [Record(d) for d in found]


def record_ids(snarfed: Path, source: str) -> list[str]:
    """Every record id of a source, from directory names alone.

    Faster than records() on a network filesystem, since it does not stat
    each record.
    """
    base = snarfed / check_source_id(source)
    if not base.is_dir():
        return []
    found = []
    for year in os.scandir(base):
        if year.is_dir():
            found.extend(d.name for d in os.scandir(year.path) if RECORD_DIR_RE.match(d.name))
    return sorted(found)


def sources(snarfed: Path) -> list[str]:
    if not snarfed.is_dir():
        return []
    return sorted(
        d.name for d in snarfed.iterdir() if d.is_dir() and d.name not in RESERVED_SOURCE_IDS
    )


def blob_contributions(snarfed: Path) -> Iterator[tuple[str, str, int, int]]:
    """Yield (source, record id, blobs, bytes) for each record, oldest first.

    Each blob is counted for the first record that carried it, so the counts
    sum to the size of the blob store.
    """
    seen: set[bytes] = set()
    for source in sources(snarfed):
        for record in records(snarfed, source):
            blobs = size = 0
            for item in record.items():
                digest = bytes.fromhex(item.sha256)
                if digest not in seen:
                    seen.add(digest)
                    blobs += 1
                    size += item.size
            yield source, record.id, blobs, size


def blob_usage(snarfed: Path) -> tuple[int, int]:
    """Return the number of distinct blobs and their total bytes."""
    blobs = total = 0
    for _, _, n, size in blob_contributions(snarfed):
        blobs += n
        total += size
    return blobs, total


def latest_record(snarfed: Path, source: str, feed: str, url: str | None = None) -> Record | None:
    """The latest record of a feed with a body, optionally for one URL."""
    for record in reversed(records(snarfed, source)):
        fetch = record.fetch()
        if fetch.feed == feed and fetch.status == 200 and (url is None or fetch.url == url):
            return record
    return None


def latest(snarfed: Path, source: str, feed: str, url: str | None = None) -> Fetch | None:
    """The latest fetch of a feed, for conditional requests."""
    record = latest_record(snarfed, source, feed, url)
    return record.fetch() if record else None


def find(snarfed: Path, source: str, record_id: str) -> Record:
    for record in records(snarfed, source):
        if record.id == record_id:
            return record
    raise ArchiveError(f"no record {record_id} for source {source}")
