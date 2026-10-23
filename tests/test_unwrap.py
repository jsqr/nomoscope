import io
import zipfile

import pytest

from nomoscope.snarf import unwrap


def make_zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def test_zip_yields_files_not_directories(tmp_path):
    path = tmp_path / "a.zip"
    make_zip(path, {"A.dat": b"one", "sub/": b"", "sub/B_1.lob": b"<xml/>"})
    got = [(name, reader.read()) for name, reader in unwrap.unwrap_zip(path, "a.zip")]
    assert got == [("A.dat", b"one"), ("sub/B_1.lob", b"<xml/>")]


def test_none_yields_the_file_under_its_given_name(tmp_path):
    path = tmp_path / "staged.part"
    path.write_bytes(b"%PDF")
    got = [(name, reader.read()) for name, reader in unwrap.unwrap_none(path, "readme.pdf")]
    assert got == [("readme.pdf", b"%PDF")]


def test_bad_zip_is_an_unwrap_error(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    with pytest.raises(unwrap.UnwrapError, match=r"bad\.zip"):
        list(unwrap.unwrap_zip(path, "bad.zip"))


def test_unknown_kind():
    with pytest.raises(unwrap.UnwrapError, match="unknown unwrapper"):
        unwrap.unwrapper("tar")
    assert unwrap.unwrapper("zip") is unwrap.unwrap_zip


def test_zip_readers_stream(tmp_path):
    path = tmp_path / "a.zip"
    make_zip(path, {"big": b"x" * 100_000})
    for _, reader in unwrap.unwrap_zip(path, "a.zip"):
        assert isinstance(reader, io.IOBase | zipfile.ZipExtFile)
        assert len(reader.read(10)) == 10
