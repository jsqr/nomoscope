"""Tables from OCR `table` blocks.

`cells` reads a block's markdown into rows of cells. `group` joins a
table that runs onto the next page: the block that ends one page and the
block that starts the next, with as many columns. `rows` gives a grouped
table's rows, without the header a continued page repeats.
"""

import re

from nomoscope.structure import Row

RUNNING = {"header", "footer"}
SEPARATOR = re.compile(r":?-+:?")


def cells(markdown: str) -> list[list[str]]:
    """The rows of a markdown table, without its separator row."""
    out = []
    for line in markdown.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        row = [c.strip() for c in line.strip("|").split("|")]
        if all(SEPARATOR.fullmatch(c) for c in row):
            continue
        out.append(row)
    return out


def _width(block: Row) -> int:
    found = cells(block["content"])
    return len(found[0]) if found else 0


def group(blocks: list[Row]) -> list[list[Row]]:
    """A document's table blocks, each list one table, in document order.

    `blocks` are all of the document's blocks, in page and reading order.
    """
    body: dict[int, list[Row]] = {}
    for b in blocks:
        if b["type"] not in RUNNING:
            body.setdefault(b["page"], []).append(b)
    out: list[list[Row]] = []
    for b in blocks:
        if b["type"] != "table":
            continue
        if out:
            prev = out[-1][-1]
            ends = body[prev["page"]][-1] is prev
            starts = b["page"] == prev["page"] + 1 and body[b["page"]][0] is b
            if ends and starts and _width(prev) == _width(b):
                out[-1].append(b)
                continue
        out.append([b])
    return out


def rows(table: list[Row]) -> list[list[str]]:
    found = [cells(b["content"]) for b in table]
    out = list(found[0])
    header = out[0] if out else None
    for more in found[1:]:
        out += more[1:] if more and more[0] == header else more
    return out


def markdown(row: list[str]) -> str:
    return "| " + " | ".join(c.replace("|", "\\|") for c in row) + " |"
