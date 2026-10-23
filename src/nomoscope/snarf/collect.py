"""Archive a tree of files as one record.

Some sources publish many small files instead of an archive file. A
collected record has one manifest line per file in the tree. Its
fetch.json names the base URL and hashes the manifest, so an unchanged
tree has the same hash.

A collector lists the files in a tree. Each source supplies its own.
"""

import hashlib
import logging
import time
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from io import BytesIO
from typing import Protocol

from nomoscope.settings import Settings
from nomoscope.snarf import archive
from nomoscope.snarf.blobs import BlobStore
from nomoscope.snarf.fetch import Entry, Fetcher, FetchError

log = logging.getLogger(__name__)

PROGRESS = 500


@dataclass(frozen=True)
class Sweep:
    """One pass over a feed.

    delay is the pause between requests. limit caps the number of files
    fetched. force fetches files the previous pass already stored.
    """

    source: str
    feed: str
    params: Mapping[str, str]
    force: bool = False
    limit: int | None = None
    delay: float = 0.0


@dataclass(frozen=True)
class Candidate:
    """A file a collector found.

    name is the file's path within the tree. size and modified come from a
    directory listing, if the source has one. data holds the bytes if the
    collector already read the file.
    """

    name: str
    url: str
    size: int | None = None
    modified: datetime | None = None
    data: bytes | None = None


class Collector(Protocol):
    def __call__(
        self, fetcher: Fetcher, base: str, params: Mapping[str, str]
    ) -> Iterator[Candidate]: ...


@dataclass(frozen=True)
class Collected:
    record: archive.Record
    items: int
    new_blobs: int
    new_bytes: int
    carried: int
    missing: int


def walk(fetcher: Fetcher, base: str, *, prefix: str = "", depth: int = 1) -> Iterator[Entry]:
    """Every file under a directory, up to depth levels down."""
    for entry in fetcher.entries(base):
        name = f"{prefix}{entry.name}"
        if not entry.is_dir:
            yield Entry(name, entry.url, False, entry.size, entry.modified)
        elif depth > 1:
            yield from walk(fetcher, entry.url, prefix=f"{name}/", depth=depth - 1)


def as_candidates(entries: Iterable[Entry]) -> Iterator[Candidate]:
    return (Candidate(e.name, e.url, e.size, e.modified) for e in entries)


def unchanged(candidate: Candidate, prior: archive.Item | None, since: datetime | None) -> bool:
    """Whether the listing shows the file the previous pass stored.

    This needs both the size and the modification time. Without them the
    file is fetched again.
    """
    return (
        prior is not None
        and since is not None
        and candidate.size == prior.size
        and candidate.modified is not None
        and candidate.modified <= since
    )


def manifest_sha256(items: Iterable[archive.Item]) -> str:
    h = hashlib.sha256()
    for item in items:
        h.update(f"{item.name}\0{item.sha256}\n".encode())
    return h.hexdigest()


def collect_record(
    cfg: Settings,
    store: BlobStore,
    fetcher: Fetcher,
    sweep: Sweep,
    base: str,
    candidates: Iterable[Candidate],
) -> Collected:
    """Fetch a tree's files into the blob store and write one record.

    Files the listing shows as unchanged keep their previous manifest entry
    and are not fetched. Files the server no longer has are counted and
    skipped.
    """
    source, feed = sweep.source, sweep.feed
    prior = None if sweep.force else archive.latest_record(cfg.snarfed, source, feed, base)
    prior_items = prior.item_map() if prior else {}
    since = prior.fetch().fetched_at if prior else None

    asked_at = datetime.now(UTC)
    items: list[archive.Item] = []
    new_blobs = new_bytes = carried = missing = 0
    for candidate in bounded(candidates, sweep.limit):
        keep = prior_items.get(candidate.name)
        if unchanged(candidate, keep, since):
            assert keep is not None
            items.append(keep)
            carried += 1
        else:
            try:
                data = candidate.data if candidate.data is not None else fetcher.read(candidate.url)
            except FetchError as e:
                log.warning("%s/%s: %s", source, feed, e)
                missing += 1
                continue
            sha256, size, written = store.put_stream(BytesIO(data))
            items.append(archive.Item(candidate.name, sha256, size))
            new_blobs += written
            new_bytes += size if written else 0
            if sweep.delay and candidate.data is None:
                time.sleep(sweep.delay)
        if len(items) % PROGRESS == 0:
            log.info("%s/%s: %d files (%d new)", source, feed, len(items), new_blobs)

    fetch = archive.Fetch(
        source=source,
        feed=feed,
        url=base,
        params=dict(sweep.params),
        timestamp=archive.format_timestamp(asked_at),
        status=200,
        etag=None,
        last_modified=None,
        sha256=manifest_sha256(items),
        size=sum(i.size for i in items),
        item_count=len(items),
    )
    record = archive.write_record(
        archive.record_dir(cfg.snarfed, source, asked_at, fetch.sha256), fetch, items
    )
    log.info(
        "%s/%s: %s (%d files, %d new blobs, %d carried, %d missing)",
        source,
        feed,
        record.id,
        len(items),
        new_blobs,
        carried,
        missing,
    )
    return Collected(record, len(items), new_blobs, new_bytes, carried, missing)


def bounded(candidates: Iterable[Candidate], limit: int | None) -> Iterator[Candidate]:
    for n, candidate in enumerate(candidates):
        if limit is not None and n >= limit:
            return
        yield candidate
