from datetime import UTC

import httpx
import pytest

from nomoscope.snarf import fetch
from nomoscope.snarf.fetch import Entry, FetchError, HttpFetcher, LocalFetcher


def test_a_local_fetcher_reads_and_lists_file_urls(tmp_path):
    (tmp_path / "b.txt").write_text("bee")
    (tmp_path / "a").mkdir()
    local = LocalFetcher()
    assert local.read((tmp_path / "b.txt").as_uri()) == b"bee"
    [a, b] = local.entries(tmp_path.as_uri())
    assert (a.name, a.is_dir, a.size) == ("a", True, None)
    assert (b.name, b.is_dir, b.size) == ("b.txt", False, 3)
    assert b.url == (tmp_path / "b.txt").as_uri()
    assert b.modified is not None and b.modified.tzinfo is UTC
    with pytest.raises(FetchError, match="read"):
        local.read((tmp_path / "absent").as_uri())
    with pytest.raises(FetchError, match="not a file url"):
        local.read("https://h/x")


def test_an_http_fetcher_lists_only_through_the_source_parser():
    def handler(request):
        if request.url.path == "/gone":
            return httpx.Response(404)
        return httpx.Response(200, text="a.txt\nb.txt\n")

    client = httpx.Client(transport=httpx.MockTransport(handler))

    def listing(body, base, zone):
        return [Entry(n, f"{base}{n}", False) for n in body.split()]

    plain = HttpFetcher(client)
    assert plain.read("https://h/x") == b"a.txt\nb.txt\n"
    with pytest.raises(FetchError, match="no listing parser"):
        plain.entries("https://h/")
    listed = HttpFetcher(client, listing=listing)
    assert [e.url for e in listed.entries("https://h/")] == [
        "https://h/a.txt",
        "https://h/b.txt",
    ]
    with pytest.raises(FetchError, match="HTTP 404"):
        plain.read("https://h/gone")


def test_the_fetcher_follows_the_scheme(tmp_path):
    with fetch.fetcher_for(tmp_path.as_uri(), None) as f:
        assert isinstance(f, LocalFetcher)
    with fetch.fetcher_for("https://h/", httpx.Client()) as f:
        assert isinstance(f, HttpFetcher)
    with (
        pytest.raises(FetchError, match="no fetcher"),
        fetch.fetcher_for("ftp://h/", None),
    ):
        pass
