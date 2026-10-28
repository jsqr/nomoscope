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
from nomoscope.pdf import ocr
from nomoscope.snarf import archive, attach, local
from nomoscope.snarf.feeds import Feed, Source
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


class Job:
    def __init__(self, status: str, output_file: str | None = None):
        self.status, self.output_file = status, output_file
        self.total_requests, self.completed_requests = 2, 0


def batch_fakes(monkeypatch, statuses: list[str], errors: dict[str, str] | None = None):
    """Patch the batch requests; return the submitted files."""
    submitted: list[list[bytes]] = []
    monkeypatch.setenv("MISTRAL_API_KEY", "key")
    monkeypatch.setattr(ocr, "process", lambda *a: pytest.fail("no single request expected"))
    monkeypatch.setattr(
        ocr, "submit", lambda client, files, model, meta: submitted.append(files) or "j1"
    )
    monkeypatch.setattr(ocr, "job", lambda client, job_id: Job(statuses.pop(0), "out"))

    def results(client, file_id):
        lines = b"\n".join(submitted[-1]).splitlines()
        names = [json.loads(line)["custom_id"] for line in lines]
        found = {n: {"model": "m", "pages": []} for n in names if n not in (errors or {})}
        return found, errors or {}

    monkeypatch.setattr(ocr, "results", results)
    return submitted


def test_ocr_batch_submits_one_job_and_attaches_its_responses(cfg, record, monkeypatch):
    submitted = batch_fakes(monkeypatch, ["SUCCESS"])
    out = invoke("ocr", "run", fake.SOURCE_ID, record.id, "--pages", "2", "--batch")
    assert out.exit_code == 0, out.output
    [files] = submitted
    assert [json.loads(line)["custom_id"] for line in b"\n".join(files).splitlines()] == [
        "00000-00002.json",
        "00002-00003.json",
    ]
    written = attach.find(cfg, fake.SOURCE_ID, record.id, "ocr")
    assert written is not None
    assert [i.name for i in written.items()] == ["00000-00002.json", "00002-00003.json"]


def test_ocr_batch_without_waiting_checks_the_same_job_later(cfg, record, monkeypatch):
    submitted = batch_fakes(monkeypatch, ["QUEUED", "SUCCESS"])
    args = ("ocr", "run", fake.SOURCE_ID, record.id, "--pages", "2", "--batch")
    out = invoke(*args, "--no-wait")
    assert out.exit_code == 0, out.output
    assert attach.find(cfg, fake.SOURCE_ID, record.id, "ocr") is None
    out = invoke(*args, "--no-wait")
    assert out.exit_code == 0, out.output
    assert len(submitted) == 1
    assert attach.find(cfg, fake.SOURCE_ID, record.id, "ocr") is not None


def test_ocr_batch_with_failed_requests_resubmits_only_those(cfg, record, monkeypatch):
    batch_fakes(monkeypatch, ["SUCCESS"], {"00002-00003.json": "too large"})
    args = ("ocr", "run", fake.SOURCE_ID, record.id, "--pages", "2", "--batch")
    out = invoke(*args)
    assert out.exit_code == 1 and "1 segments missing" in out.output
    assert attach.find(cfg, fake.SOURCE_ID, record.id, "ocr") is None
    submitted = batch_fakes(monkeypatch, ["SUCCESS"])
    out = invoke(*args)
    assert out.exit_code == 0, out.output
    [[resubmitted]] = submitted
    assert json.loads(resubmitted)["custom_id"] == "00002-00003.json"
    assert attach.find(cfg, fake.SOURCE_ID, record.id, "ocr") is not None


def test_batch_files_stay_under_the_limit():
    lines = [b"a" * 4, b"b" * 4, b"c" * 20, b"d"]
    assert ocr.batch_files(lines, limit=10) == [b"aaaa\nbbbb", b"c" * 20, b"d"]


def test_batch_output_parses_responses_and_errors():
    data = b"\n".join(
        json.dumps(line).encode()
        for line in [
            {"custom_id": "a", "response": {"status_code": 200, "body": {"pages": []}}},
            {"custom_id": "b", "response": {"status_code": 400, "body": {"message": "bad"}}},
            {"custom_id": "c", "error": {"message": "timeout"}},
        ]
    )
    responses, errors = ocr.output(data)
    assert responses == {"a": {"pages": []}}
    assert set(errors) == {"b", "c"}
