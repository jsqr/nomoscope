"""Fetch one feed and write its records.

A feed of files downloads and unwraps each one. A feed of a tree passes
its collector to collect, which writes one record for the tree.
"""

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx

from nomoscope.settings import Settings
from nomoscope.snarf import archive, collect, fetch, http, unwrap
from nomoscope.snarf.blobs import BlobStore
from nomoscope.snarf.collect import Sweep
from nomoscope.snarf.feeds import Feed, Source

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Outcome:
    feed: str
    url: str
    status: int
    record: archive.Record | None
    items: int
    new_blobs: int
    new_bytes: int  # of the new blobs only; what the store grew by

    @property
    def snarfed(self) -> bool:
        return self.record is not None


def blob_store(cfg: Settings) -> BlobStore:
    return BlobStore(cfg.snarfed / "blobs")


def snarf_url(
    cfg: Settings,
    source: Source,
    feed: Feed,
    url: str,
    sweep: Sweep,
    *,
    client: httpx.Client,
) -> Outcome:
    prior = None if sweep.force else archive.latest(cfg.snarfed, source.id, feed.name, url)
    asked_at = datetime.now(UTC)
    dl = http.download(
        client,
        url,
        cfg.staging,
        etag=prior.etag if prior else None,
        last_modified=prior.last_modified if prior else None,
    )
    if not dl.has_body:
        reason = "not modified" if dl.status == 304 else "not found"
        log.info("%s/%s: %s (%s)", source.id, feed.name, reason, url)
        return Outcome(feed.name, url, dl.status, None, 0, 0, 0)

    assert dl.path is not None and dl.sha256 is not None
    if prior is not None and prior.sha256 == dl.sha256:
        log.info("%s/%s: identical bytes to previous fetch", source.id, feed.name)

    store = blob_store(cfg)
    items: list[archive.Item] = []
    new_blobs = new_bytes = 0
    try:
        for name, reader in unwrap.unwrapper(feed.unwrap)(dl.path, basename(url)):
            sha256, size, written = store.put_stream(reader)
            items.append(archive.Item(name, sha256, size))
            new_blobs += written
            new_bytes += size if written else 0
        fetch = archive.Fetch(
            source=source.id,
            feed=feed.name,
            url=url,
            params=dict(sweep.params),
            timestamp=archive.format_timestamp(asked_at),
            status=dl.status,
            etag=dl.etag,
            last_modified=dl.last_modified,
            sha256=dl.sha256,
            size=dl.size,
            item_count=len(items),
        )
        record = archive.write_record(
            archive.record_dir(cfg.snarfed, source.id, asked_at, dl.sha256),
            fetch,
            items,
        )
    finally:
        dl.discard()
    log.info(
        "%s/%s: %s (%d items, %d new blobs)",
        source.id,
        feed.name,
        record.id,
        len(items),
        new_blobs,
    )
    return Outcome(feed.name, url, dl.status, record, len(items), new_blobs, new_bytes)


def snarf_feed(
    cfg: Settings,
    source: Source,
    feed: Feed,
    params: Mapping[str, str],
    *,
    client: httpx.Client,
    force: bool = False,
    limit: int | None = None,
    delay: float | None = None,
) -> list[Outcome]:
    """limit caps the files fetched: candidates for a tree, URLs otherwise."""
    sweep = Sweep(
        source.id,
        feed.name,
        params,
        force,
        limit,
        feed.delay if delay is None else delay,
    )
    urls = feed.resolve(params)
    if feed.collect is not None:
        return [collect_url(cfg, source, feed, url, sweep, client=client) for url in urls]
    return [snarf_url(cfg, source, feed, url, sweep, client=client) for url in urls[:limit]]


def collect_url(
    cfg: Settings,
    source: Source,
    feed: Feed,
    url: str,
    sweep: Sweep,
    *,
    client: httpx.Client,
) -> Outcome:
    assert feed.collect is not None
    zone = ZoneInfo(source.zone)
    with fetch.fetcher_for(url, client, zone, feed.listing) as fetcher:
        collected = collect.collect_record(
            cfg,
            blob_store(cfg),
            fetcher,
            sweep,
            url,
            feed.collect(fetcher, url, sweep.params),
        )
    return Outcome(
        feed.name,
        url,
        200,
        collected.record,
        collected.items,
        collected.new_blobs,
        collected.new_bytes,
    )


def basename(url: str) -> str:
    return Path(urlsplit(url).path).name
