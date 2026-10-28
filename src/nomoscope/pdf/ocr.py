"""OCR a PDF in segments of pages, and read the responses' layout blocks.

A segment is a range of pages sent as one request. A segment's record
keeps the whole response with the PDF's name and hash and the segment's
first page, so blocks can be read again without a request.

`process` sends one segment and waits for its response. A batch job
sends many: `submit` uploads their requests and creates the job,
`job` reads its progress, and `results` reads its output. A batch job
finishes within its timeout, a day by default. These are the functions
that make requests, and the requests are billed; their client is a
`mistralai.Mistral` (the `ocr` extra).
"""

import base64
import io
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader, PdfWriter

from nomoscope.rows import Row

MODEL = "mistral-ocr-latest"
# A request takes up to 1000 pages or 50 MB. Small segments keep an
# interrupted run cheap to resume.
SEGMENT_PAGES = 8
BATCH_ENDPOINT = "/v1/ocr"
# Each input file of a batch job stays under the upload limit.
BATCH_FILE_BYTES = 100_000_000
# The statuses of a batch job that has not finished.
BATCH_RUNNING = frozenset({"QUEUED", "RUNNING", "CANCELLATION_REQUESTED"})


@dataclass(frozen=True)
class Segment:
    first: int
    last: int  # exclusive

    @property
    def name(self) -> str:
        return f"{self.first:05d}-{self.last:05d}.json"


def segments(pages: int, size: int = SEGMENT_PAGES) -> list[Segment]:
    return [Segment(first, min(first + size, pages)) for first in range(0, pages, size)]


def extract(reader: PdfReader, seg: Segment) -> bytes:
    """The segment's pages as a PDF of their own."""
    writer = PdfWriter()
    for i in range(seg.first, seg.last):
        writer.add_page(reader.pages[i])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def body(pdf: bytes) -> dict[str, Any]:
    """A request's fields besides the model, with the PDF sent inline."""
    encoded = base64.b64encode(pdf).decode()
    return {
        "document": {
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{encoded}",
        },
        "table_format": "markdown",
    }


def process(client: Any, pdf: bytes, model: str = MODEL) -> dict[str, Any]:
    """OCR a PDF and return the response as JSON data."""
    return client.ocr.process(model=model, **body(pdf)).model_dump(mode="json")


def batch_line(custom_id: str, pdf: bytes) -> bytes:
    return json.dumps({"custom_id": custom_id, "body": body(pdf)}).encode()


def batch_files(lines: Iterable[bytes], limit: int = BATCH_FILE_BYTES) -> list[bytes]:
    """Join request lines into JSONL files of at most `limit` bytes; a longer line goes alone."""
    files: list[bytes] = []
    current: list[bytes] = []
    size = 0
    for line in lines:
        if current and size + len(line) + 1 > limit:
            files.append(b"\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        files.append(b"\n".join(current))
    return files


def submit(client: Any, files: list[bytes], model: str, metadata: Mapping[str, str]) -> str:
    """Upload a batch's input files, create its job, and return the job id."""
    ids = [
        client.files.upload(
            file={"file_name": f"batch-{i:03d}.jsonl", "content": data}, purpose="batch"
        ).id
        for i, data in enumerate(files)
    ]
    created = client.batch.jobs.create(
        endpoint=BATCH_ENDPOINT, input_files=ids, model=model, metadata=dict(metadata)
    )
    return created.id


def job(client: Any, job_id: str) -> Any:
    """The job's state: `status`, request counts, and `output_file` when done."""
    return client.batch.jobs.get(job_id=job_id)


def running(status: str) -> bool:
    return status in BATCH_RUNNING


def results(client: Any, file_id: str) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """A finished job's responses and errors, by custom id."""
    data = client.files.download(file_id=file_id).read()
    return output(data)


def output(data: bytes) -> tuple[dict[str, dict[str, Any]], dict[str, str]]:
    """Parse a batch output file: responses and errors, by custom id."""
    responses: dict[str, dict[str, Any]] = {}
    errors: dict[str, str] = {}
    for line in data.splitlines():
        if not line.strip():
            continue
        found = json.loads(line)
        cid = found["custom_id"]
        response = found.get("response") or {}
        status = response.get("status_code")
        if found.get("error") or status is None or status >= 400:
            errors[cid] = json.dumps(found.get("error") or response.get("body"))
        else:
            responses[cid] = response["body"]
    return responses, errors


def record(response: dict[str, Any], name: str, sha256: str, seg: Segment) -> bytes:
    """A segment's response with where it came from, as stored."""
    found = {
        "source": name,
        "sha256": sha256,
        "first_page": seg.first,
        "n_pages": seg.last - seg.first,
        "response": response,
    }
    return json.dumps(found).encode()


def block_rows(stored: dict[str, Any]) -> list[Row]:
    """One row per layout block of a stored segment, in reading order within each page."""
    return [
        {
            "source": stored["source"],
            "sha256": stored["sha256"],
            "page": stored["first_page"] + page["index"],
            "ordinal": i,
            "type": block["type"],
            "x0": block["top_left_x"],
            "y0": block["top_left_y"],
            "x1": block["bottom_right_x"],
            "y1": block["bottom_right_y"],
            "width": (page.get("dimensions") or {}).get("width"),
            "height": (page.get("dimensions") or {}).get("height"),
            "dpi": (page.get("dimensions") or {}).get("dpi"),
            "content": block["content"],
        }
        for page in stored["response"]["pages"]
        for i, block in enumerate(page.get("blocks") or [])
    ]
