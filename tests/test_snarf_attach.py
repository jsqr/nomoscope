import io
import json

import pytest
from pypdf import PdfWriter
from typer.testing import CliRunner

from nomoscope import cli
from nomoscope.crunch import runner
from nomoscope.crunch.registry import Registry
from nomoscope.graph import records
from nomoscope.graph.store import Store
from nomoscope.snarf import archive, attach, local
from nomoscope.snarf.feeds import Feed, Source
from nomoscope.structure import ocr
from tests import fake

OCR = Feed("ocr", (), "none", days=(), completeness="full", crunchable=False)
SOURCE = Source(fake.SOURCE_ID, (fake.CODE, OCR), (), lambda d: {})
REGISTRY = Registry(sources={fake.SOURCE_ID: SOURCE})
JURISDICTION = "us-il-windytown"


def pdf_bytes(pages: int = 3) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def record(cfg, tmp_path) -> archive.Record:
    folder = tmp_path / "code"
    folder.mkdir()
    (folder / "code.pdf").write_bytes(pdf_bytes())
    out = local.import_path(
        cfg, SOURCE, fake.CODE, folder, {"jurisdiction": JURISDICTION, "code": "municipal"}
    )
    assert out.record is not None
    return out.record


def test_an_attached_record_belongs_to_its_record_and_is_not_crunched(cfg, record):
    written = attach.write(cfg, SOURCE, record, "ocr", {"00000-00003.json": b"{}"}, {"model": "m"})
    params = written.fetch().params
    assert (params["of"], params["jurisdiction"], params["model"]) == (record.id, JURISDICTION, "m")
    assert attach.find(cfg, fake.SOURCE_ID, record.id, "ocr") == written
    assert attach.index(cfg, fake.SOURCE_ID) == {record.id: [written]}
    store = Store(cfg)
    runner.register(cfg, store, REGISTRY)
    with store.open(read_only=True) as conn:
        crunchable = {r.record: r.crunchable for r in records.records(conn)}
    assert crunchable == {record.id: True, written.id: False}


def invoke(*args: str):
    return CliRunner().invoke(cli.app(REGISTRY, fake.APP), list(args))


def segment(sha256: str, first: int = 0) -> bytes:
    response = {"model": "m", "pages": [{"index": 0, "blocks": []}]}
    return json.dumps(
        {
            "source": "x.pdf",
            "sha256": sha256,
            "first_page": first,
            "n_pages": 3,
            "response": response,
        }
    ).encode()


def test_ocr_dry_run_makes_no_request(cfg, record, monkeypatch):
    monkeypatch.setattr(ocr, "process", lambda *a: pytest.fail("no request expected"))
    out = invoke("ocr", "run", fake.SOURCE_ID, record.id, "--dry-run", "--pages", "2")
    assert out.exit_code == 0, out.output
    assert "code.pdf: 3 pages in 2 requests" in out.output
    assert attach.find(cfg, fake.SOURCE_ID, record.id, "ocr") is None


def test_ocr_run_writes_one_record_of_segments(cfg, record, monkeypatch):
    calls = []
    monkeypatch.setenv("MISTRAL_API_KEY", "key")
    monkeypatch.setattr(
        ocr,
        "process",
        lambda client, pdf, model: calls.append(model) or {"model": model, "pages": []},
    )
    out = invoke("ocr", "run", fake.SOURCE_ID, record.id, "--pages", "2")
    assert out.exit_code == 0, out.output
    written = attach.find(cfg, fake.SOURCE_ID, record.id, "ocr")
    assert written is not None
    assert [i.name for i in written.items()] == ["00000-00002.json", "00002-00003.json"]
    assert calls == [ocr.MODEL, ocr.MODEL]


def test_ocr_import_checks_the_pdf(cfg, record, tmp_path):
    [item] = record.items()
    good, bad = tmp_path / "good", tmp_path / "bad"
    good.mkdir()
    bad.mkdir()
    (good / "00000-00003.json").write_bytes(segment(item.sha256))
    (bad / "00000-00003.json").write_bytes(segment("0" * 64))
    out = invoke("ocr", "import", fake.SOURCE_ID, record.id, str(bad))
    assert out.exit_code == 1 and "made from another file" in out.output
    out = invoke("ocr", "import", fake.SOURCE_ID, record.id, str(good), "--importer", "unpdf")
    assert out.exit_code == 0, out.output
    written = attach.find(cfg, fake.SOURCE_ID, record.id, "ocr")
    assert written is not None
    assert written.fetch().params["importer"] == "unpdf"


def test_block_rows():
    stored = {
        "source": "x.pdf", "sha256": "s", "first_page": 8,
        "response": {"pages": [{"index": 1, "dimensions": {"dpi": 93, "width": 791, "height": 1023},
                                "blocks": [{"type": "table", "top_left_x": 1, "top_left_y": 2,
                                            "bottom_right_x": 3, "bottom_right_y": 4,
                                            "content": "| a |"}]}]},
    }  # fmt: skip
    [row] = ocr.block_rows(stored)
    assert (row["page"], row["ordinal"], row["type"], row["dpi"], row["content"]) == (
        9,
        0,
        "table",
        93,
        "| a |",
    )
