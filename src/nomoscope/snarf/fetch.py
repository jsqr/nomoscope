"""Read files and list directories, by URL.

HttpFetcher lists a directory only with a parser the source supplies.
LocalFetcher reads file:// URLs. Both hold a whole file in memory. Large
downloads go through snarf.http instead.
"""

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, tzinfo
from pathlib import Path
from typing import Protocol
from urllib.parse import unquote, urlsplit

import httpx

from nomoscope.errors import NomoscopeError


class FetchError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    url: str
    is_dir: bool
    size: int | None = None
    modified: datetime | None = None


class Fetcher(Protocol):
    def read(self, url: str) -> bytes: ...

    def entries(self, url: str) -> list[Entry]: ...


Listing = Callable[[str, str, tzinfo], list[Entry]]
"""A listing parser: the page body, its URL, and the zone its times are in."""


@dataclass(frozen=True)
class HttpFetcher:
    client: httpx.Client
    zone: tzinfo = UTC
    listing: Listing | None = None

    def response(self, url: str) -> httpx.Response:
        try:
            r = self.client.get(url)
        except httpx.HTTPError as e:
            raise FetchError(f"GET {url}: {e}") from e
        if r.status_code != 200:
            raise FetchError(f"GET {url}: HTTP {r.status_code}")
        return r

    def read(self, url: str) -> bytes:
        return self.response(url).content

    def entries(self, url: str) -> list[Entry]:
        if self.listing is None:
            raise FetchError(f"no listing parser for {url}")
        return self.listing(self.response(url).text, url, self.zone)


def file_path(url: str) -> Path:
    parts = urlsplit(url)
    if parts.scheme != "file":
        raise FetchError(f"not a file url: {url}")
    return Path(unquote(parts.path))


@dataclass(frozen=True)
class LocalFetcher:
    """Files on this machine. Modification times are read in UTC."""

    def read(self, url: str) -> bytes:
        try:
            return file_path(url).read_bytes()
        except OSError as e:
            raise FetchError(f"read {url}: {e}") from e

    def entries(self, url: str) -> list[Entry]:
        base = file_path(url)
        try:
            found = sorted(os.scandir(base), key=lambda d: d.name)
        except OSError as e:
            raise FetchError(f"list {url}: {e}") from e
        entries = []
        for d in found:
            path = Path(d.path)
            stat = d.stat()
            entries.append(
                Entry(
                    name=d.name,
                    url=path.as_uri(),
                    is_dir=d.is_dir(),
                    size=None if d.is_dir() else stat.st_size,
                    modified=datetime.fromtimestamp(stat.st_mtime, UTC),
                )
            )
        return entries


@contextmanager
def fetcher_for(
    url: str,
    client: httpx.Client | None,
    zone: tzinfo = UTC,
    listing: Listing | None = None,
) -> Iterator[Fetcher]:
    scheme = urlsplit(url).scheme
    if scheme == "file":
        yield LocalFetcher()
    elif scheme in ("http", "https") and client is not None:
        yield HttpFetcher(client, zone, listing)
    else:
        raise FetchError(f"no fetcher for scheme {scheme!r}: {url}")
