from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nomoscope.snarf import archive
from nomoscope.snarf.archive import FETCH_FILE, MANIFEST_FILE, RECORD_DIR_RE

TS = datetime(2026, 9, 6, 15, 30, 54, tzinfo=UTC)
SHA = "4473fc01" + "a" * 56
TS_ID = "2026-09-06T153054Z-4473fc01"


def fetch(feed="daily", status=200, url="https://x/a.zip", ts=TS, sha=SHA):
    return archive.Fetch(
        source="us-ca-leginfo",
        feed=feed,
        url=url,
        params={},
        timestamp=archive.format_timestamp(ts),
        status=status,
        etag='"abc"',
        last_modified="Sun, 06 Sep 2026 11:23:32 GMT",
        sha256=sha,
        size=3,
        item_count=1,
    )


def test_record_dir_naming(tmp_path):
    path = archive.record_dir(tmp_path, "us-ca-leginfo", TS, SHA)
    assert path == tmp_path / "us-ca-leginfo" / "2026" / "2026-09-06T153054Z-4473fc01"


def test_record_dir_normalizes_to_utc(tmp_path):
    from datetime import timedelta, timezone

    local = TS.astimezone(timezone(timedelta(hours=-7)))
    assert archive.record_dir(tmp_path, "s", local, SHA) == archive.record_dir(
        tmp_path, "s", TS, SHA
    )


@pytest.mark.parametrize("bad", ["blobs", "US-CA", "", "-x", "a_b", "a/b"])
def test_source_ids_rejected(bad):
    with pytest.raises(archive.ArchiveError, match="invalid source id"):
        archive.check_source_id(bad)


def test_write_and_read_back(tmp_path):
    path = archive.record_dir(tmp_path, "us-ca-leginfo", TS, SHA)
    items = [archive.Item("A.dat", "0" * 64, 10), archive.Item("B_1.lob", "1" * 64, 20)]
    record = archive.write_record(path, fetch(), items)
    assert record.id == "2026-09-06T153054Z-4473fc01"
    assert record.fetch() == fetch()
    assert archive.record_dir(tmp_path, "us-ca-leginfo", TS, SHA) == path.with_name(
        path.name + "-2"
    )
    assert record.fetch().fetched_at == TS
    assert list(record.items()) == items
    assert record.item_map()["B_1.lob"].size == 20
    with pytest.raises(archive.ArchiveError, match="already exists"):
        archive.write_record(path, fetch(), items)
    second = archive.write_record(path.with_name(path.name + "-2"), fetch(), items)
    assert [r.id for r in archive.records(tmp_path, "us-ca-leginfo")] == [
        record.id,
        second.id,
    ]


def test_records_are_ordered_across_years_and_latest_filters(tmp_path):
    early = datetime(2025, 12, 31, 23, 59, 59, tzinfo=UTC)
    later = datetime(2026, 1, 1, 0, 0, 1, tzinfo=UTC)
    for ts, feed, status, sha in [
        (early, "biennial", 200, "1" * 64),
        (TS, "daily", 200, "2" * 64),
        (later, "daily", 304, "3" * 64),
    ]:
        f = fetch(feed=feed, status=status, ts=ts, sha=sha)
        archive.write_record(archive.record_dir(tmp_path, "s", ts, sha), f, [])
    (tmp_path / "s" / "2026" / "not-a-record").mkdir()

    ids = [r.id for r in archive.records(tmp_path, "s")]
    assert ids == [
        "2025-12-31T235959Z-11111111",
        "2026-01-01T000001Z-33333333",
        "2026-09-06T153054Z-22222222",
    ]
    assert archive.latest(tmp_path, "s", "daily").sha256 == "2" * 64
    assert archive.latest_record(tmp_path, "s", "daily").id == "2026-09-06T153054Z-22222222"
    assert archive.latest(tmp_path, "s", "biennial").sha256 == "1" * 64
    assert archive.latest(tmp_path, "s", "daily", url="https://elsewhere") is None
    assert archive.latest(tmp_path, "s", "nope") is None
    assert archive.find(tmp_path, "s", "2026-09-06T153054Z-22222222").fetch().feed == "daily"
    with pytest.raises(archive.ArchiveError, match="no record"):
        archive.find(tmp_path, "s", "missing")


def test_records_order_by_second_before_mtime(tmp_path):
    first = datetime(2026, 9, 6, 15, 30, 50, tzinfo=UTC)
    second = first + timedelta(seconds=1)
    for ts, sha in [(second, "2" * 64), (first, "1" * 64)]:
        f = fetch(ts=ts, sha=sha)
        archive.write_record(archive.record_dir(tmp_path, "s", ts, sha), f, [])
    ids = [r.id for r in archive.records(tmp_path, "s")]
    assert ids == ["2026-09-06T153050Z-11111111", "2026-09-06T153051Z-22222222"]


def test_sources_skips_blobs(tmp_path):
    for name in ("blobs", "us-ca-leginfo", "ny-bills"):
        (tmp_path / name).mkdir()
    assert archive.sources(tmp_path) == ["ny-bills", "us-ca-leginfo"]
    assert archive.records(tmp_path, "nothing-here") == []


def test_blob_usage_counts_each_blob_once(tmp_path):
    shared = archive.Item(name="a.txt", sha256="aa" * 32, size=10)
    only = archive.Item(name="b.txt", sha256="bb" * 32, size=5)
    archive.write_record(
        archive.record_dir(tmp_path, "us-ca-leginfo", TS, SHA),
        fetch(),
        [shared, only],
    )
    archive.write_record(
        archive.record_dir(tmp_path, "us-ca-leginfo", TS + timedelta(days=1), SHA),
        fetch(ts=TS + timedelta(days=1)),
        [shared],
    )
    assert archive.blob_usage(tmp_path) == (2, 15)


def test_blob_usage_of_empty_archive(tmp_path):
    assert archive.blob_usage(tmp_path) == (0, 0)


def test_blob_contributions_credit_each_blob_to_the_record_that_brought_it(tmp_path):
    shared = archive.Item(name="a.txt", sha256="aa" * 32, size=10)
    only = archive.Item(name="b.txt", sha256="bb" * 32, size=5)
    first = archive.record_dir(tmp_path, "us-ca-leginfo", TS, SHA)
    archive.write_record(first, fetch(), [shared, only])
    later = TS + timedelta(days=1)
    second = archive.record_dir(tmp_path, "us-ca-leginfo", later, SHA)
    archive.write_record(second, fetch(ts=later), [shared])
    assert list(archive.blob_contributions(tmp_path)) == [
        ("us-ca-leginfo", first.name, 2, 15),
        ("us-ca-leginfo", second.name, 0, 0),
    ]


def test_record_ids_lists_without_reading_a_record(tmp_path, monkeypatch):
    first = archive.record_dir(tmp_path, "us-ca-leginfo", TS, SHA)
    archive.write_record(first, fetch(), [])
    later = TS + timedelta(days=400)
    second = archive.record_dir(tmp_path, "us-ca-leginfo", later, SHA)
    archive.write_record(second, fetch(ts=later), [])
    (tmp_path / "us-ca-leginfo" / "2026" / "not-a-record").mkdir()

    stat = Path.stat

    def refuse(self, *a, **k):
        if RECORD_DIR_RE.match(self.name) or self.name in (FETCH_FILE, MANIFEST_FILE):
            raise AssertionError(f"record_ids must not stat {self}")
        return stat(self, *a, **k)

    monkeypatch.setattr(Path, "stat", refuse)
    assert archive.record_ids(tmp_path, "us-ca-leginfo") == sorted([first.name, second.name])
    assert archive.record_ids(tmp_path, "nothing-here") == []
