"""Conditional, streaming HTTP downloads into the staging directory."""

import hashlib
import logging
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx

from nomoscope.errors import NomoscopeError

log = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(30.0, read=300.0)
CHUNK = 1 << 20


class DownloadError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Download:
    url: str
    status: int
    path: Path | None
    sha256: str | None
    size: int
    etag: str | None
    last_modified: str | None

    @property
    def has_body(self) -> bool:
        return self.status == 200

    def discard(self) -> None:
        if self.path is not None:
            self.path.unlink(missing_ok=True)


def make_client(user_agent: str, headers: Mapping[str, str] | None = None) -> httpx.Client:
    """Return a client that sends user_agent and headers with every request.

    Some publishers refuse httpx's default user agent.
    """
    return httpx.Client(
        timeout=TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": user_agent, **(headers or {})},
    )


def download(
    client: httpx.Client,
    url: str,
    staging: Path,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> Download:
    """Download url into a file under staging, hashing it as it streams.

    Sends If-None-Match and If-Modified-Since when given. Returns a 304 or
    404 without a file. Raises on any other status that is not 200.
    """
    headers = {}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    staging.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url, headers=headers) as response:
        status = response.status_code
        if status in (304, 404):
            return Download(url, status, None, None, 0, None, None)
        if status != 200:
            raise DownloadError(f"GET {url}: HTTP {status}")

        h = hashlib.sha256()
        size = 0
        with tempfile.NamedTemporaryFile(dir=staging, suffix=".part", delete=False) as tmp:
            try:
                for chunk in response.iter_bytes(CHUNK):
                    h.update(chunk)
                    size += len(chunk)
                    tmp.write(chunk)
            except BaseException:
                Path(tmp.name).unlink(missing_ok=True)
                raise
        log.info("downloaded %s (%d bytes)", url, size)
        return Download(
            url=url,
            status=status,
            path=Path(tmp.name),
            sha256=h.hexdigest(),
            size=size,
            etag=response.headers.get("etag"),
            last_modified=response.headers.get("last-modified"),
        )
