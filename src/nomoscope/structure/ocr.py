"""OCR a PDF in segments of pages, and read the responses' layout blocks.

A segment is a range of pages sent as one request. A segment's record
keeps the whole response with the PDF's name and hash and the segment's
first page, so blocks can be read again without a request. `process` is
the only function here that makes a request, and the request is billed;
its client is a `mistralai.Mistral` (the `ocr` extra).
"""

import base64
import io
import json
from dataclasses import dataclass
from typing import Any

from pypdf import PdfReader, PdfWriter

from nomoscope.structure import Row

MODEL = "mistral-ocr-latest"
# A request takes up to 1000 pages or 50 MB. Small segments keep an
# interrupted run cheap to resume.
SEGMENT_PAGES = 8


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


def process(client: Any, pdf: bytes, model: str = MODEL) -> dict[str, Any]:
    """OCR a PDF, sent inline, and return the response as JSON data."""
    encoded = base64.b64encode(pdf).decode()
    response = client.ocr.process(
        model=model,
        document={"type": "document_url", "document_url": f"data:application/pdf;base64,{encoded}"},
        table_format="markdown",
    )
    return response.model_dump(mode="json")


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
