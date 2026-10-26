"""Build one text document per division, with offsets for its subdivisions.

A division's document is its own text and its subdivisions', in reading
order, without its label and heading lines. Lines are joined with a
space, or a newline at a paragraph gap. A subdivision's span runs from
its label to the end of its last descendant.
"""

import hashlib
import itertools
import re
from dataclasses import dataclass

from nomoscope.structure import Row
from nomoscope.structure.parse import starts_paragraph

SECTION = "section"
WORD = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
BROKEN = re.compile(r"([A-Za-z]+)-$")
CONTINUED = re.compile(r"[a-z]+")
# The shortest half of a broken word taken as a word of its own.
WHOLE = 3


@dataclass(frozen=True)
class Vocabulary:
    """The document's words and hyphenated compounds, in lower case."""

    words: frozenset[str]
    compounds: frozenset[str]


def vocabulary(lines: list[Row]) -> Vocabulary:
    """The words of `lines`, without the halves of words broken across two lines."""
    found: list[str] = []
    broken = False
    for line in lines:
        words = WORD.findall(line.get("text") or "")
        if broken:
            words = words[1:]
        broken = bool(BROKEN.search((line.get("text") or "").rstrip()))
        if broken:
            words = words[:-1]
        found.extend(w.lower() for w in words)
    words = {p for w in found for p in w.split("-")}
    compounds = {"-".join(pair) for w in found for pair in itertools.pairwise(w.split("-"))}
    return Vocabulary(frozenset(words), frozenset(compounds))


@dataclass(frozen=True)
class Join:
    text: str  # the text, ready to take the next piece with no space
    guessed: bool  # the document had no evidence; the grammar's default decided


def rejoined(text: str, piece: str, vocab: Vocabulary, hyphens: str) -> Join | None:
    """Join `piece` to `text`, if `piece` finishes the word broken at the end of `text`."""
    tail, head = BROKEN.search(text), CONTINUED.match(piece)
    if tail is None or head is None:
        return None
    first, rest = tail.group(1).lower(), head.group()
    if first + rest in vocab.words:
        return Join(text[:-1], False)
    if f"{first}-{rest}" in vocab.compounds:
        return Join(text, False)
    # Two whole words, as "three-" "tiered", make a compound; a syllable seldom is a word.
    halves = len(first) >= WHOLE and len(rest) >= WHOLE
    if hyphens == "syllables" and halves and first in vocab.words and rest in vocab.words:
        return Join(text, True)
    return Join(text[:-1] if hyphens == "syllables" else text, True)


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


def _separator(line: Row, index: int, previous: int, node: Row) -> str:
    """Join pieces of one line with a space, and start a paragraph or a subdivision on a new line.

    A page's first line has no gap above it, so a subdivision label there
    is known by its node, not its gap.
    """
    if index == previous:
        return " "
    opens = node["kind"] == "subdivision" and node["line_first"] == index
    return "\n" if opens or starts_paragraph(line) else " "


def build(
    nodes: list[Row], lines: list[Row], hyphens: str = "keep"
) -> tuple[list[Row], list[Row], list[Row]]:
    """Return the documents, the subdivision spans, and the source line of each piece of text.

    Unless `hyphens` is "keep", a word broken across two lines of one node is
    joined (see `rejoined`).
    """
    by_id = {n["id"]: n for n in nodes}
    vocab = vocabulary(lines) if hyphens != "keep" else None
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
        pieces = sorted(owned.get(n["id"], []))
        for k, (index, start, node_id) in enumerate(pieces):
            line = lines[index]
            # Stacked labels ("A. (1) Text") put two nodes' pieces on one line.
            later = pieces[k + 1] if k + 1 < len(pieces) else None
            end = later[1] if later is not None and later[0] == index else None
            piece = (line.get("text") or "").strip()[start:end].strip()
            if text:
                previous = pieces[k - 1]
                gap = _separator(line, index, previous[0], by_id[node_id])
                joined = None
                if vocab and gap == " " and index != previous[0] and node_id == previous[2]:
                    joined = rejoined(text, piece, vocab, hyphens)
                if joined is not None:
                    back[-1]["end_char"] = len(joined.text)
                    text, gap = joined.text, ""
                text += gap
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
