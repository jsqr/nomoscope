import io
import zipfile

import pytest

from nomoscope.settings import Settings
from nomoscope.snarf import archive, local
from nomoscope.snarf.feeds import Feed, Source

FILE = Feed("code", ("",), "none", days=(), completeness="full")
ZIPPED = Feed("bundle", ("",), "zip", days=(), completeness="full")
TREE = Feed("tree", ("",), "none", days=(), completeness="full")
SRC = Source("manual", (FILE, ZIPPED, TREE), (), lambda d: {})
PARAMS = {"jurisdiction": "us-il-windytown"}


@pytest.fixture
def cfg(tmp_path):
    return Settings(data_root=tmp_path / "root")


def test_a_file_imports_as_a_record_with_its_provenance(cfg, tmp_path):
    path = tmp_path / "code.txt"
    path.write_text("Sec. 1-1. Title.\n")
    out = local.import_path(cfg, SRC, FILE, path, PARAMS, importer="jj")
    assert out.snarfed and (out.items, out.new_blobs) == (1, 1)
    fetch = out.record.fetch()
    assert fetch.url == path.resolve().as_uri()
    assert fetch.last_modified is None
    assert fetch.params["jurisdiction"] == "us-il-windytown"
    assert fetch.params["importer"] == "jj"
    assert "mtime" in fetch.params
    [item] = out.record.items()
    assert item.name == "code.txt"
    assert local.blob_store(cfg).read(item.sha256) == b"Sec. 1-1. Title.\n"


def test_reimporting_the_same_bytes_writes_nothing_unless_forced(cfg, tmp_path):
    path = tmp_path / "code.txt"
    path.write_text("one\n")
    first = local.import_path(cfg, SRC, FILE, path, PARAMS, importer="jj")
    again = local.import_path(cfg, SRC, FILE, path, PARAMS, importer="jj")
    assert not again.snarfed and again.status == local.NOT_MODIFIED
    forced = local.import_path(cfg, SRC, FILE, path, PARAMS, importer="jj", force=True)
    assert forced.snarfed and forced.new_blobs == 0
    path.write_text("two\n")
    changed = local.import_path(cfg, SRC, FILE, path, PARAMS, importer="jj")
    assert changed.snarfed
    ids = [r.id for r in archive.records(cfg.snarfed, "manual")]
    assert ids == [first.record.id, forced.record.id, changed.record.id]


def test_a_zip_imports_its_members_and_not_the_container(cfg, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("a.txt", "a")
        zf.writestr("b/c.txt", "c")
    path = tmp_path / "bundle.zip"
    path.write_bytes(buf.getvalue())
    out = local.import_path(cfg, SRC, ZIPPED, path, PARAMS, importer="jj")
    assert [i.name for i in out.record.items()] == ["a.txt", "b/c.txt"]
    assert out.record.fetch().sha256 != out.record.item_map()["a.txt"].sha256
    blobs = list((cfg.snarfed / "blobs" / "sha256").rglob("*"))
    assert len([b for b in blobs if b.is_file()]) == 2


def test_a_tree_imports_as_one_record_named_by_relative_paths(cfg, tmp_path):
    root = tmp_path / "tree"
    (root / "ch1").mkdir(parents=True)
    (root / "ch1" / "s1.txt").write_text("one")
    (root / "s0.txt").write_text("zero")
    (root / ".DS_Store").write_text("junk")
    out = local.import_path(cfg, SRC, TREE, root, PARAMS, importer="jj")
    assert [i.name for i in out.record.items()] == ["ch1/s1.txt", "s0.txt"]
    assert out.record.fetch().url == root.resolve().as_uri()
    assert "mtime" not in out.record.fetch().params
    again = local.import_path(cfg, SRC, TREE, root, PARAMS, importer="jj")
    assert not again.snarfed
    (root / "s0.txt").write_text("zero, amended")
    changed = local.import_path(cfg, SRC, TREE, root, PARAMS, importer="jj")
    assert changed.snarfed and changed.new_blobs == 1
