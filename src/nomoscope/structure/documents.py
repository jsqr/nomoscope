"""Build one text document per division, with offsets for its subdivisions.

A division's document is its own text and its subdivisions', in reading
order, without its label and heading lines. Lines are joined with a
space, or a newline at a paragraph gap. A subdivision's span runs from
its label to the end of its last descendant.
"""

import hashlib

from nomoscope.structure import Row
from nomoscope.structure.parse import starts_paragraph

SECTION = "section"


def _owner(node: Row, by_id: dict[str, Row]) -> Row:
    """Return the division, or the root, whose document holds `node`'s text."""
    while node["kind"] == "subdivision":
        node = by_id[node["parent_id"]]
    return node


def citation(node: Row) -> str:
    """Return a section's number, or any other division's node id."""
    if node["level"] == SECTION and node["number"]:
        return node["number"]
    return node["id"]


def build(nodes: list[Row], lines: list[Row]) -> tuple[list[Row], list[Row], list[Row]]:
    """Return the documents, the subdivision spans, and the source line of each piece of text."""
    by_id = {n["id"]: n for n in nodes}
    owned: dict[str, list[tuple[int, int, str]]] = {}
    for n in nodes:
        doc = _owner(n, by_id)["id"]
        owned.setdefault(doc, []).extend((p["line"], p["start"], n["id"]) for p in n["pieces"])

    docs, back = [], []
    spans: dict[str, list[int]] = {}  # node id -> [start, end] in its document
    for n in nodes:
        if n["kind"] == "subdivision":
            continue
        text = ""
        for index, start, node_id in sorted(owned.get(n["id"], [])):
            line = lines[index]
            piece = (line.get("text") or "").strip()[start:]
            if text:
                text += "\n" if starts_paragraph(line) else " "
            first, text = len(text), text + piece
            spans.setdefault(node_id, [first, first])[1] = len(text)
            back.append(
                {"document": n["id"], "start_char": first, "end_char": len(text),
                 "index": index, "page": line["page"], "line": line.get("ordinal"),
                 "x0": line.get("x0"), "x1": line.get("x1"), "y": line.get("y")}
            )  # fmt: skip
        pages = [lines[i]["page"] for i, _, _ in owned.get(n["id"], [])]
        docs.append(
            {
                "node_id": n["id"],
                "citation": citation(n),
                "level": n["level"],
                "number": n["number"],
                "heading": n["heading"],
                "text": text,
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "notes": _notes(n, nodes, by_id),
                "page_first": min(pages, default=n["page_first"]),
                "page_last": max(pages, default=n["page_last"]),
            }
        )

    # A subdivision's span runs to the end of its last descendant. Nodes come
    # in document order, so walking them backward reaches children first.
    for n in reversed(nodes):
        parent = by_id.get(n["parent_id"])
        if n["id"] in spans and parent is not None and parent["kind"] == "subdivision":
            ps = spans.setdefault(parent["id"], list(spans[n["id"]]))
            ps[0], ps[1] = min(ps[0], spans[n["id"]][0]), max(ps[1], spans[n["id"]][1])
    subs = []
    for n in nodes:
        if n["kind"] != "subdivision":
            continue
        doc = _owner(n, by_id)
        s, e = spans.get(n["id"], (None, None))
        subs.append(
            {"node_id": n["id"], "document": doc["id"],
             "path": n["id"][len(doc["id"]) + 1 :] if doc["id"] else n["id"],
             "level": n["level"], "number": n["number"], "start_char": s, "end_char": e}
        )  # fmt: skip
    return docs, subs, back


def _notes(division: Row, nodes: list[Row], by_id: dict[str, Row]) -> list[str]:
    return [
        note
        for n in nodes
        if n["kind"] == "subdivision" or n["id"] == division["id"]
        if _owner(n, by_id)["id"] == division["id"]
        for note in n["notes"]
    ]
