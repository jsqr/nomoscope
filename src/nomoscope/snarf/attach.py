"""Records attached to another record, such as the OCR responses for a fetched PDF.

An attached record is archived like a fetch: its files go to the blob
store, and its `fetch.json` names the record it belongs to (`of`) and
carries that record's params, so it registers under the same
jurisdiction and code. Its feed is not crunchable, so it is never routed
to a cruncher; a cruncher reads it beside the record it belongs to.
"""

from collections.abc import Mapping
from datetime import UTC, datetime

from nomoscope.settings import Settings
from nomoscope.snarf import archive
from nomoscope.snarf.collect import manifest_sha256
from nomoscope.snarf.feeds import Source
from nomoscope.snarf.run import blob_store

OF = "of"


def write(
    cfg: Settings,
    source: Source,
    of: archive.Record,
    feed: str,
    files: Mapping[str, bytes],
    params: Mapping[str, str] | None = None,
) -> archive.Record:
    source.feed(feed)
    store = blob_store(cfg)
    items = [archive.Item(name, store.put(data), len(data)) for name, data in sorted(files.items())]
    now = datetime.now(UTC)
    fetch = archive.Fetch(
        source=source.id,
        feed=feed,
        url=f"attached:{of.id}/{feed}",
        params={**of.fetch().params, **(params or {}), OF: of.id},
        timestamp=archive.format_timestamp(now),
        status=200,
        etag=None,
        last_modified=None,
        sha256=manifest_sha256(items),
        size=sum(i.size for i in items),
        item_count=len(items),
    )
    path = archive.record_dir(cfg.snarfed, source.id, now, fetch.sha256)
    return archive.write_record(path, fetch, items)


def find(cfg: Settings, source: str, of: str, feed: str) -> archive.Record | None:
    """The latest record of `feed` attached to record `of`."""
    for record in reversed(archive.records(cfg.snarfed, source)):
        fetch = record.fetch()
        if fetch.feed == feed and fetch.params.get(OF) == of:
            return record
    return None


def index(cfg: Settings, source: str) -> dict[str, list[archive.Record]]:
    """The latest attached record of each feed, by the id of the record it belongs to."""
    latest: dict[tuple[str, str], archive.Record] = {}
    for record in archive.records(cfg.snarfed, source):
        fetch = record.fetch()
        if of := fetch.params.get(OF):
            latest[of, fetch.feed] = record
    out: dict[str, list[archive.Record]] = {}
    for (of, _), record in sorted(latest.items()):
        out.setdefault(of, []).append(record)
    return out
