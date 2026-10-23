import io
import zipfile

import httpx
import pytest

from nomoscope.settings import Settings
from nomoscope.snarf import archive, run
from nomoscope.snarf.feeds import Feed, Source


def zip_bytes(members):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


FEED = Feed("inc", ("https://h/pubinfo_{day}.zip",), "zip", days=(0,), completeness="partial")
DOCS = Feed("docs", ("https://h/a.pdf", "https://h/b.pdf"), "none", days=(), completeness="full")
SRC = Source("src", (FEED, DOCS), ("docs",), lambda d: {"day": "Mon"})


class Server:
    """A mock server with a mutable body that answers conditional requests by ETag."""

    def __init__(self, body, etag='"1"'):
        self.body = body
        self.etag = etag
        self.requests = []

    def __call__(self, request):
        self.requests.append(request)
        if request.url.path.endswith("missing.zip"):
            return httpx.Response(404)
        if request.headers.get("if-none-match") == self.etag:
            return httpx.Response(304)
        return httpx.Response(
            200, content=self.body, headers={"ETag": self.etag, "Last-Modified": "Mon"}
        )

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def cfg(tmp_path):
    return Settings(data_root=tmp_path)


def test_first_fetch_writes_record_and_blobs(cfg):
    server = Server(zip_bytes({"A.dat": b"row\n", "A_1.lob": b"<x/>"}))
    [out] = run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client())
    assert out.snarfed and out.status == 200
    assert (out.items, out.new_blobs) == (2, 2)
    assert out.new_bytes == sum(i.size for i in out.record.items())

    fetch = out.record.fetch()
    assert (fetch.feed, fetch.url) == ("inc", "https://h/pubinfo_Mon.zip")
    assert fetch.params == {"day": "Mon"}
    assert (fetch.etag, fetch.last_modified, fetch.item_count) == ('"1"', "Mon", 2)
    assert out.record.path.parent.parent == cfg.snarfed / "src"

    store = run.blob_store(cfg)
    items = out.record.item_map()
    assert store.read(items["A.dat"].sha256) == b"row\n"
    assert store.read(items["A_1.lob"].sha256) == b"<x/>"
    assert list(cfg.staging.iterdir()) == []


def test_second_fetch_is_conditional_and_a_noop(cfg):
    server = Server(zip_bytes({"A.dat": b"row\n"}))
    run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client())
    [out] = run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client())
    assert out.status == 304 and not out.snarfed
    assert server.requests[-1].headers["if-none-match"] == '"1"'
    assert server.requests[-1].headers["if-modified-since"] == "Mon"
    assert len(archive.records(cfg.snarfed, "src")) == 1


def test_force_skips_conditional_headers_and_dedupes_blobs(cfg):
    server = Server(zip_bytes({"A.dat": b"row\n"}))
    run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client())
    [out] = run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client(), force=True)
    assert "if-none-match" not in server.requests[-1].headers
    assert out.snarfed and out.new_blobs == 0 and out.items == 1
    assert out.new_bytes == 0  # the same bytes again grow the store by nothing
    ids = [r.id for r in archive.records(cfg.snarfed, "src")]
    assert len(ids) == 2 and ids[1] == ids[0] + "-2"


def test_changed_content_makes_a_new_record(cfg):
    server = Server(zip_bytes({"A.dat": b"row\n"}))
    run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client())
    server.body = zip_bytes({"A.dat": b"row\nrow2\n"})
    server.etag = '"2"'
    [out] = run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client())
    assert out.snarfed and out.new_blobs == 1
    assert out.new_bytes == len(b"row\nrow2\n")
    assert archive.latest(cfg.snarfed, "src", "inc").etag == '"2"'


def test_404_is_a_noop(cfg):
    feed = Feed("gone", ("https://h/missing.zip",), "zip", days=(0,), completeness="partial")
    [out] = run.snarf_feed(cfg, SRC, feed, {}, client=Server(b"").client())
    assert out.status == 404 and not out.snarfed
    assert archive.records(cfg.snarfed, "src") == []


def test_multi_url_feed_records_each_url_separately(cfg):
    server = Server(b"%PDF")
    outs = run.snarf_feed(cfg, SRC, DOCS, {}, client=server.client())
    assert [o.url for o in outs] == ["https://h/a.pdf", "https://h/b.pdf"]
    assert [list(o.record.item_map()) for o in outs] == [["a.pdf"], ["b.pdf"]]
    assert outs[1].new_blobs == 0
    assert (
        archive.latest(cfg.snarfed, "src", "docs", url="https://h/b.pdf").url == "https://h/b.pdf"
    )


def test_unwrap_failure_discards_staged_file_and_writes_nothing(cfg):
    server = Server(b"not a zip")
    with pytest.raises(Exception, match=r"pubinfo_Mon\.zip"):
        run.snarf_feed(cfg, SRC, FEED, {"day": "Mon"}, client=server.client())
    assert list(cfg.staging.iterdir()) == []
    assert archive.records(cfg.snarfed, "src") == []
