"""Import local files into the archive.

An imported file or directory becomes a record with a file:// URL. A file
goes through the feed's unwrapper. A directory becomes one record with a
manifest line per file.

The params record the importer and, for a file, its modification time.
The modification time is not used as the publication date. Importing the
same bytes again writes nothing.
"""

import getpass
import logging
import os
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path

from nomoscope.graph import fingerprint
from nomoscope.settings import Settings
from nomoscope.snarf import archive, collect, unwrap
from nomoscope.snarf.feeds import Feed, Source
from nomoscope.snarf.run import Outcome, blob_store

log = logging.getLogger(__name__)

NOT_MODIFIED = 304


def import_path(
    cfg: Settings,
    source: Source,
    feed: Feed,
    path: Path,
    params: Mapping[str, str],
    *,
    importer: str | None = None,
    force: bool = False,
) -> Outcome:
    path = path.expanduser().resolve()
    who = importer or getpass.getuser()
    if path.is_dir():
        return import_tree(cfg, source, feed, path, params, importer=who, force=force)
    return import_file(cfg, source, feed, path, params, importer=who, force=force)


def import_file(
    cfg: Settings,
    source: Source,
    feed: Feed,
    path: Path,
    params: Mapping[str, str],
    *,
    importer: str,
    force: bool = False,
) -> Outcome:
    url = path.as_uri()
    with path.open("rb") as reader:
        sha256, size = fingerprint.hash_stream(reader)
    prior = archive.latest(cfg.snarfed, source.id, feed.name, url)
    if prior is not None and prior.sha256 == sha256 and not force:
        log.info("%s/%s: unchanged since %s (%s)", source.id, feed.name, prior.timestamp, url)
        return Outcome(feed.name, url, NOT_MODIFIED, None, 0, 0, 0)

    store = blob_store(cfg)
    items: list[archive.Item] = []
    new_blobs = new_bytes = 0
    for name, reader in unwrap.unwrapper(feed.unwrap)(path, path.name):
        item_sha, item_size, written = store.put_stream(reader)
        items.append(archive.Item(name, item_sha, item_size))
        new_blobs += written
        new_bytes += item_size if written else 0
    mtime = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    provenance = {"importer": importer, "mtime": archive.format_timestamp(mtime)}
    return write(
        cfg,
        source,
        feed,
        url,
        dict(params) | provenance,
        sha256,
        size,
        items,
        new_blobs,
        new_bytes,
    )


def import_tree(
    cfg: Settings,
    source: Source,
    feed: Feed,
    root: Path,
    params: Mapping[str, str],
    *,
    importer: str,
    force: bool = False,
) -> Outcome:
    url = root.as_uri()
    store = blob_store(cfg)
    items: list[archive.Item] = []
    new_blobs = new_bytes = 0
    for name, path in files(root):
        with path.open("rb") as reader:
            sha256, size, written = store.put_stream(reader)
        items.append(archive.Item(name, sha256, size))
        new_blobs += written
        new_bytes += size if written else 0
    sha256 = collect.manifest_sha256(items)
    prior = archive.latest(cfg.snarfed, source.id, feed.name, url)
    if prior is not None and prior.sha256 == sha256 and not force:
        log.info("%s/%s: unchanged since %s (%s)", source.id, feed.name, prior.timestamp, url)
        return Outcome(feed.name, url, NOT_MODIFIED, None, 0, 0, 0)
    size = sum(i.size for i in items)
    provenance = {"importer": importer}
    return write(
        cfg,
        source,
        feed,
        url,
        dict(params) | provenance,
        sha256,
        size,
        items,
        new_blobs,
        new_bytes,
    )


def files(root: Path) -> Iterator[tuple[str, Path]]:
    """Every file under root as (relative name, path), sorted. Skips hidden files."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for f in filenames:
            if not f.startswith("."):
                path = Path(dirpath) / f
                found.append((path.relative_to(root).as_posix(), path))
    yield from sorted(found)


def write(
    cfg: Settings,
    source: Source,
    feed: Feed,
    url: str,
    params: Mapping[str, str],
    sha256: str,
    size: int,
    items: list[archive.Item],
    new_blobs: int,
    new_bytes: int,
) -> Outcome:
    asked_at = datetime.now(UTC)
    fetch = archive.Fetch(
        source=source.id,
        feed=feed.name,
        url=url,
        params=dict(params),
        timestamp=archive.format_timestamp(asked_at),
        status=200,
        etag=None,
        last_modified=None,
        sha256=sha256,
        size=size,
        item_count=len(items),
    )
    record = archive.write_record(
        archive.record_dir(cfg.snarfed, source.id, asked_at, sha256), fetch, items
    )
    log.info(
        "%s/%s: %s (%d items, %d new blobs)",
        source.id,
        feed.name,
        record.id,
        len(items),
        new_blobs,
    )
    return Outcome(feed.name, url, 200, record, len(items), new_blobs, new_bytes)
