"""Read a PDF's bookmarks and text layer as rows.

Coordinates are PDF points from the top-left corner of the page, which
match OCR coordinates scaled by dpi / 72.
"""

import math
from typing import Any

from pypdf import PageObject, PdfReader
from pypdf.generic import Destination

from nomoscope.rows import Row

BOLD = ("Bold", "Black", "Heavy", "Semibold", "Demi")
ITALIC = ("Italic", "Oblique")
INVISIBLE = 3


def outline_rows(reader: PdfReader, source: str, digest: str) -> list[Row]:
    """Return one row per bookmark, in outline order. Depth 0 is the top level."""
    rows = []

    def walk(items: list[Any], depth: int) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            page = reader.get_destination_page_number(item)
            height = float(reader.pages[page].mediabox.top) if page is not None else None
            top = item.top if isinstance(item, Destination) else None
            rows.append(
                {
                    "source": source,
                    "sha256": digest,
                    "ordinal": len(rows),
                    "depth": depth,
                    "title": item.title,
                    "page": page,
                    "top": None if top is None or height is None else height - float(top),
                }
            )

    walk(reader.outline, 0)
    return rows


def _mult(m: list[float], n: list[float]) -> list[float]:
    return [
        m[0] * n[0] + m[1] * n[2],
        m[0] * n[1] + m[1] * n[3],
        m[2] * n[0] + m[3] * n[2],
        m[2] * n[1] + m[3] * n[3],
        m[4] * n[0] + m[5] * n[2] + n[4],
        m[4] * n[1] + m[5] * n[3] + n[5],
    ]


def _scale(m: list[float]) -> float:
    """The text matrix's vertical scale; rotated text has none on the diagonal."""
    return math.hypot(m[2], m[3]) or math.hypot(m[0], m[1])


def page_spans(page: PageObject, page_no: int, source: str, digest: str) -> list[Row]:
    """Return one row per visible text fragment on a page, in content-stream order.

    Invisible text (render mode 3), a scanner's OCR layer, is skipped.
    """
    box = page.mediabox
    rows = []
    # Render mode is graphics state: q saves it, Q restores it.
    mode = [0]

    def operand(op, args, cm, tm):
        if op == b"q":
            mode.append(mode[-1])
        elif op == b"Q" and len(mode) > 1:
            mode.pop()
        elif op == b"Tr" and args:
            mode[-1] = int(args[0])

    def visit(text, cm, tm, font, size):
        if not text.strip() or mode[-1] == INVISIBLE:
            return
        m = _mult(tm, cm)
        name = str((font or {}).get("/BaseFont", "")).lstrip("/").split("+")[-1]
        rows.append(
            {
                "source": source,
                "sha256": digest,
                "page": page_no,
                "ordinal": len(rows),
                "page_width": float(box.width),
                "page_height": float(box.height),
                "x": round(m[4] - float(box.left), 2),
                "y": round(float(box.top) - m[5], 2),
                "size": round(size * _scale(m), 2),
                "font": name,
                "bold": any(s in name for s in BOLD),
                "italic": any(s in name for s in ITALIC),
                "text": text.strip(),
            }
        )

    page.extract_text(visitor_operand_before=operand, visitor_text=visit)
    return rows


def span_rows(reader: PdfReader, source: str, digest: str) -> list[Row]:
    return [
        row
        for page_no, page in enumerate(reader.pages)
        for row in page_spans(page, page_no, source, digest)
    ]
