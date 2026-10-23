from datetime import date

import pytest

from nomoscope.snarf.feeds import Feed, FeedError, Source


def test_feed_resolves_templates():
    feed = Feed(
        "x",
        ("https://h/{year}.zip", "https://h/{day}.zip"),
        "zip",
        days=(),
        completeness="full",
    )
    assert feed.resolve({"year": "2025", "day": "Mon"}) == [
        "https://h/2025.zip",
        "https://h/Mon.zip",
    ]
    with pytest.raises(FeedError, match="missing parameter 'day'"):
        feed.resolve({"year": "2025"})


def test_feed_validates_days_and_completeness():
    with pytest.raises(FeedError, match="days"):
        Feed("x", ("u",), "zip", days=(7,), completeness="full")
    with pytest.raises(FeedError, match="days"):
        Feed("x", ("u",), "zip", days=(1, 1), completeness="full")
    with pytest.raises(FeedError, match="completeness"):
        Feed("x", ("u",), "zip", days=(), completeness="some")


def test_source_due_by_weekday():
    weekly = Feed("weekly", ("u",), "zip", days=(6,), completeness="full")
    daily = Feed("daily", ("v",), "zip", days=(0, 1, 2, 3, 4, 5), completeness="partial")
    never = Feed("never", ("w",), "zip", days=(), completeness="full")
    src = Source("s", (never, weekly, daily), (), lambda d: {})
    assert src.due(date(2026, 9, 6)) == ("weekly",)
    assert src.due(date(2026, 9, 4)) == ("daily",)
    both = Source(
        "s",
        (weekly, Feed("also", ("x",), "zip", days=(6,), completeness="full")),
        (),
        lambda d: {},
    )
    assert both.due(date(2026, 9, 6)) == ("weekly", "also")


def test_source_lookup_and_validation():
    a = Feed("a", ("u",), "zip", days=(), completeness="full")
    src = Source("s", (a,), ("a",), lambda d: {})
    assert src.feed("a") is a
    with pytest.raises(FeedError, match="no feed 'b'"):
        src.feed("b")
    with pytest.raises(FeedError, match="duplicate"):
        Source("s", (a, a), (), lambda d: {})
    with pytest.raises(FeedError, match="no feed 'zz'"):
        Source("s", (a,), ("zz",), lambda d: {})


def test_a_record_registers_from_its_params_unless_the_source_resolves_it(tmp_path):
    from nomoscope.graph import records
    from nomoscope.snarf import archive

    weekly = Feed("weekly", ("u",), "none", days=(), completeness="partial")
    fetch = archive.Fetch(
        source="s",
        feed="weekly",
        url="u",
        params={"jurisdiction": "us-il-elgin", "code": "zoning"},
        timestamp="2026-09-01T12:00:00Z",
        status=200,
        etag=None,
        last_modified=None,
        sha256="a" * 64,
        size=1,
        item_count=0,
    )
    record = archive.write_record(tmp_path / "2026-09-01T120000Z-aaaaaaaa", fetch, [])
    plain = Source("s", (weekly,), (), lambda d: {})
    got = plain.registration(record)
    assert (got.jurisdiction, got.code, got.session, got.completeness) == (
        "us-il-elgin",
        "zoning",
        None,
        "partial",
    )

    def everything(rec, f):
        return records.registration(
            rec, f, jurisdiction="us-ca", completeness="full", crunchable=True, session="2025"
        )

    resolved = Source("s", (weekly,), (), lambda d: {}, resolve=everything)
    assert (resolved.registration(record).jurisdiction, resolved.registration(record).code) == (
        "us-ca",
        "",
    )
    bare = archive.Fetch(**{**fetch.__dict__, "params": {}})
    unnamed = archive.write_record(tmp_path / "2026-09-02T120000Z-bbbbbbbb", bare, [])
    with pytest.raises(FeedError, match="no jurisdiction param"):
        plain.registration(unnamed)
