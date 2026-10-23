import hashlib
from io import BytesIO

import pytest

from nomoscope.snarf.blobs import BlobError, BlobStore


def test_path_fans_out_on_first_two_hex(tmp_path):
    store = BlobStore(tmp_path)
    sha = "ab" + "0" * 62
    assert store.path(sha) == tmp_path / "sha256" / "ab" / sha


def test_put_returns_sha256_and_round_trips(tmp_path):
    store = BlobStore(tmp_path)
    sha = store.put(b"hello")
    assert sha == hashlib.sha256(b"hello").hexdigest()
    assert store.read(sha) == b"hello"


def test_put_dedupes_and_reports_whether_written(tmp_path):
    store = BlobStore(tmp_path)
    sha1, size1, written1 = store.put_stream(BytesIO(b"same bytes"))
    mtime = store.path(sha1).stat().st_mtime_ns
    sha2, size2, written2 = store.put_stream(BytesIO(b"same bytes"))
    assert (sha1, size1, written1) == (sha2, size2, True)
    assert written2 is False
    assert store.path(sha1).stat().st_mtime_ns == mtime
    assert list((tmp_path / "tmp").iterdir()) == []


def test_bad_hash_rejected(tmp_path):
    store = BlobStore(tmp_path)
    with pytest.raises(BlobError, match="hex digest"):
        store.path("ABCD")
    with pytest.raises(BlobError, match="no blob"):
        store.read("0" * 64)
