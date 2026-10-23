from datetime import UTC, datetime, timedelta

import pytest

from nomoscope.settings import Settings
from nomoscope.snarf import archive, collect
from nomoscope.snarf.fetch import Entry, FetchError
from nomoscope.snarf.run import blob_store

T0 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
BASE = "https://h/tree/"


class FakeFetcher:
    """Serves a fixed tree and counts the requests."""

    def __init__(self, files: dict[str, bytes], listings: dict[str, list[Entry]] | None = None):
        self.files = files
        self.listings = listings or {}
        self.reads: list[str] = []

    def read(self, url: str) -> bytes:
        self.reads.append(url)
        try:
            return self.files[url]
        except KeyError:
            raise FetchError(f"GET {url}: HTTP 404") from None

    def entries(self, url: str) -> list[Entry]:
        return self.listings[url]


def candidates(*names, size=None, modified=None):
    return [collect.Candidate(n, BASE + n, size, modified) for n in names]


def collected(cfg, fetcher, cands, **kw):
    sweep = collect.Sweep("us-xx-test", "tree", {}, **kw)
    return collect.collect_record(cfg, blob_store(cfg), fetcher, sweep, BASE, cands)


@pytest.fixture
def cfg(tmp_path):
    return Settings(data_root=tmp_path)


def test_one_record_holds_every_file(cfg):
    fetcher = FakeFetcher({BASE + "a": b"A", BASE + "b/c": b"C"})
    out = collected(cfg, fetcher, candidates("a", "b/c"))
    assert (out.items, out.new_blobs, out.missing) == (2, 2, 0)
    assert out.new_bytes == sum(i.size for i in out.record.items())
    fetch = out.record.fetch()
    assert (fetch.url, fetch.item_count, fetch.size) == (BASE, 2, 2)
    assert [i.name for i in out.record.items()] == ["a", "b/c"]
    assert blob_store(cfg).read(out.record.item_map()["b/c"].sha256) == b"C"


def test_the_record_hash_is_the_manifest(cfg):
    files = {BASE + "a": b"A"}
    first = collected(cfg, FakeFetcher(files), candidates("a"))
    second = collected(cfg, FakeFetcher(files), candidates("a"), force=True)
    assert first.record.fetch().sha256 == second.record.fetch().sha256
    assert first.record.id != second.record.id


def test_an_unchanged_file_is_carried_forward_not_refetched(cfg):
    files = {BASE + "a": b"A", BASE + "b": b"B"}
    collected(
        cfg,
        FakeFetcher(files),
        candidates("a", "b", size=1, modified=T0 - timedelta(days=1)),
    )

    fetcher = FakeFetcher(files)
    out = collected(
        cfg,
        fetcher,
        [
            # unchanged: same size, modified before the first pass
            collect.Candidate("a", BASE + "a", 1, T0 - timedelta(days=1)),
            # changed: modified after the first pass
            collect.Candidate("b", BASE + "b", 1, datetime.now(UTC)),
        ],
    )
    assert (out.carried, out.items) == (1, 2)
    assert fetcher.reads == [BASE + "b"]


def test_a_file_with_no_listing_facts_is_always_fetched(cfg):
    files = {BASE + "a": b"A"}
    collected(cfg, FakeFetcher(files), candidates("a"))
    fetcher = FakeFetcher(files)
    out = collected(cfg, fetcher, candidates("a"))
    assert (out.carried, fetcher.reads) == (0, [BASE + "a"])


def test_a_file_the_source_no_longer_serves_is_counted_and_skipped(cfg):
    out = collected(cfg, FakeFetcher({BASE + "a": b"A"}), candidates("a", "gone"))
    assert (out.items, out.missing) == (1, 1)
    assert [i.name for i in out.record.items()] == ["a"]


def test_limit_bounds_a_pass(cfg):
    fetcher = FakeFetcher({BASE + n: b"x" for n in "abcde"})
    out = collected(cfg, fetcher, candidates(*"abcde"), limit=2)
    assert out.items == 2
    assert fetcher.reads == [BASE + "a", BASE + "b"]


def test_walk_descends_the_given_depth():
    listings = {
        BASE: [
            Entry("d", BASE + "d/", True),
            Entry("top.txt", BASE + "top.txt", False, 3),
        ],
        BASE + "d/": [Entry("leaf.txt", BASE + "d/leaf.txt", False, 4)],
    }
    fetcher = FakeFetcher({}, listings)
    assert [e.name for e in collect.walk(fetcher, BASE, depth=1)] == ["top.txt"]
    # Listing order is kept: a directory's files appear where it was listed.
    assert [e.name for e in collect.walk(fetcher, BASE, depth=2)] == [
        "d/leaf.txt",
        "top.txt",
    ]


def test_records_from_a_collection_are_found_by_feed(cfg):
    out = collected(cfg, FakeFetcher({BASE + "a": b"A"}), candidates("a"))
    assert archive.latest_record(cfg.snarfed, "us-xx-test", "tree", BASE).id == out.record.id
