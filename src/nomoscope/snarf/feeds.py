"""Sources and feeds: what to fetch, and when.

A feed is a set of URL templates, an unwrapper, and the weekdays it is
published. A feed for a tree of files has a collect function, a delay
between requests, and optionally a parser for directory listings.

A source is a set of feeds under one id. Source.registration reads a
record's fetch.json and returns its jurisdiction, code, session, and
completeness. By default these come from the record's params. A source
can supply resolve to read them another way. Bump version when
registration changes.

headers returns extra request headers, such as an API key.
"""

from collections.abc import Callable, Mapping
from dataclasses import KW_ONLY, dataclass, field
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from nomoscope.errors import NomoscopeError
from nomoscope.graph import records
from nomoscope.graph.records import Registration
from nomoscope.settings import Settings
from nomoscope.snarf.archive import Fetch, Record, check_source_id
from nomoscope.snarf.collect import Collector
from nomoscope.snarf.fetch import Listing

WEEKDAYS = range(7)
COMPLETENESS = ("full", "partial")

Resolve = Callable[[Record, Fetch], Registration]
Headers = Callable[[Settings], Mapping[str, str]]


class FeedError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Feed:
    name: str
    urls: tuple[str, ...]
    unwrap: str
    _: KW_ONLY
    days: tuple[int, ...]
    completeness: str
    collect: Collector | None = None
    listing: Listing | None = None
    delay: float = 0.0
    # False for a feed of attached records (snarf/attach.py), which no cruncher reads alone.
    crunchable: bool = True

    def __post_init__(self) -> None:
        if any(d not in WEEKDAYS for d in self.days) or len(set(self.days)) != len(self.days):
            raise FeedError(f"{self.name}: days must be distinct weekday numbers 0-6")
        if self.completeness not in COMPLETENESS:
            raise FeedError(f"{self.name}: completeness must be one of {COMPLETENESS}")
        if self.collect is not None and self.unwrap != "none":
            raise FeedError(f"{self.name}: a collected feed unwraps nothing")

    def resolve(self, params: Mapping[str, str]) -> list[str]:
        try:
            return [url.format(**params) for url in self.urls]
        except KeyError as e:
            raise FeedError(f"{self.name}: missing parameter {e.args[0]!r}") from None


@dataclass(frozen=True)
class Source:
    id: str
    feeds: tuple[Feed, ...]
    cold_start: tuple[str, ...]
    defaults: Callable[[date], dict[str, str]]
    today: Callable[[], date] = field(default=date.today)
    zone: str = "UTC"
    version: str = "1"
    resolve: Resolve | None = None
    headers: Headers | None = None

    def __post_init__(self) -> None:
        check_source_id(self.id)
        try:
            ZoneInfo(self.zone)
        except (ValueError, ZoneInfoNotFoundError) as e:
            raise FeedError(f"{self.id}: unknown time zone {self.zone!r}") from e
        names = [f.name for f in self.feeds]
        if len(set(names)) != len(names):
            raise FeedError(f"{self.id}: duplicate feed names")
        for name in self.cold_start:
            self.feed(name)

    def due(self, d: date) -> tuple[str, ...]:
        return tuple(f.name for f in self.feeds if d.weekday() in f.days)

    def feed(self, name: str) -> Feed:
        for feed in self.feeds:
            if feed.name == name:
                return feed
        raise FeedError(f"{self.id}: no feed {name!r}; feeds are {[f.name for f in self.feeds]}")

    def registration(self, record: Record) -> Registration:
        fetch = record.fetch()
        if self.resolve is not None:
            return self.resolve(record, fetch)
        params = fetch.params
        jurisdiction = params.get("jurisdiction")
        if not jurisdiction:
            raise FeedError(f"{self.id}/{record.id}: no jurisdiction param to register by")
        return records.registration(
            record,
            fetch,
            jurisdiction=jurisdiction,
            code=params.get("code", ""),
            session=params.get("session"),
            completeness=params.get("completeness", self.feed(fetch.feed).completeness),
            crunchable=self.feed(fetch.feed).crunchable,
        )

    def request_headers(self, cfg: Settings) -> Mapping[str, str]:
        return self.headers(cfg) if self.headers is not None else {}
