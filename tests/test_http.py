import hashlib

import httpx
import pytest

from nomoscope.snarf import http

BODY = b"PK\x03\x04 not really a zip"


def client_for(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_200_streams_to_staging_and_hashes(tmp_path):
    seen = {}

    def handler(request):
        seen["headers"] = request.headers
        return httpx.Response(
            200,
            content=BODY,
            headers={"ETag": '"e1"', "Last-Modified": "Sun, 06 Sep 2026"},
        )

    got = http.download(client_for(handler), "https://x/a.zip", tmp_path)
    assert got.has_body
    assert got.path.parent == tmp_path
    assert got.path.read_bytes() == BODY
    assert got.sha256 == hashlib.sha256(BODY).hexdigest()
    assert got.size == len(BODY)
    assert (got.etag, got.last_modified) == ('"e1"', "Sun, 06 Sep 2026")
    assert "if-none-match" not in seen["headers"]
    got.discard()
    assert list(tmp_path.iterdir()) == []


def test_conditional_headers_and_304(tmp_path):
    seen = {}

    def handler(request):
        seen["inm"] = request.headers.get("if-none-match")
        seen["ims"] = request.headers.get("if-modified-since")
        return httpx.Response(304)

    got = http.download(
        client_for(handler),
        "https://x/a.zip",
        tmp_path,
        etag='"e1"',
        last_modified="Sun",
    )
    assert (seen["inm"], seen["ims"]) == ('"e1"', "Sun")
    assert got.status == 304
    assert not got.has_body
    assert got.path is None
    assert list(tmp_path.iterdir()) == []


def test_404_is_returned_not_raised(tmp_path):
    got = http.download(client_for(lambda r: httpx.Response(404)), "https://x/none", tmp_path)
    assert got.status == 404
    assert got.path is None


def test_other_statuses_raise(tmp_path):
    with pytest.raises(http.DownloadError, match="HTTP 503"):
        http.download(client_for(lambda r: httpx.Response(503)), "https://x/a.zip", tmp_path)


def test_failed_stream_leaves_no_partial_file(tmp_path):
    def broken():
        yield b"abc"
        raise httpx.ReadError("boom")

    def handler(request):
        return httpx.Response(200, stream=_Gen(broken()))

    with pytest.raises(httpx.ReadError):
        http.download(client_for(handler), "https://x/a.zip", tmp_path)
    assert list(tmp_path.iterdir()) == []


class _Gen(httpx.SyncByteStream):
    def __init__(self, gen):
        self._gen = gen

    def __iter__(self):
        yield from self._gen


def test_the_client_names_itself_and_sends_the_sources_headers():
    with http.make_client("proj/1 (ops@example.org)", {"X-Api-Key": "k"}) as client:
        assert client.headers["User-Agent"] == "proj/1 (ops@example.org)"
        assert client.headers["X-Api-Key"] == "k"
