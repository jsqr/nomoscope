"""Turn a parsed tree into law_sections and law_nodes rows.

Sections are the divisions at the grammar's deepest division level.
Every other division is a heading row. A section's text is its document
(`documents.py`) without the paragraphs `[sections]` drops, and its
trailing history paragraphs go to `history`. Treepaths are node ids.
"""

import re
from dataclasses import dataclass

from nomoscope.structure import Row, documents
from nomoscope.structure import grammar as gr


@dataclass(frozen=True)
class Carved:
    sections: list[Row]
    nodes: list[Row]


def section_level(grammar: gr.Grammar) -> str | None:
    divisions = [lv.name for lv in grammar.levels if lv.kind == "division"]
    return divisions[-1] if divisions else None


@dataclass(frozen=True)
class Split:
    text: str
    history: str | None
    dropped: list[str]


def split(text: str, rules: gr.Sections) -> Split:
    """Split a section's text into text, history, and dropped paragraphs."""
    drop = [re.compile(r) for r in rules.drop]
    history = [re.compile(r) for r in rules.history]
    kept, dropped = [], []
    for para in text.split("\n"):
        (dropped if any(r.search(para) for r in drop) else kept).append(para)
    note: list[str] = []
    while kept and any(r.search(kept[-1]) for r in history):
        note.insert(0, kept.pop())
    return Split("\n".join(kept), "\n".join(note) or None, dropped)


def carve(nodes: list[Row], lines: list[Row], grammar: gr.Grammar) -> Carved:
    level = section_level(grammar)
    docs, _, back = documents.build(nodes, lines)
    by_doc = {d["node_id"]: d for d in docs}
    lines_of: dict[str, list[int]] = {}
    for b in back:
        lines_of.setdefault(b["document"], []).append(b["index"])

    sections = []
    for n in nodes:
        if n["kind"] != "division" or n["level"] != level:
            continue
        doc = by_doc[n["id"]]
        parts = split(doc["text"], grammar.sections)
        indexes = lines_of.get(n["id"], [])
        sections.append(
            {
                "section_num": n["number"] or n["id"],
                "node_treepath": n["parent_id"] or None,
                "heading": n["heading"],
                "history": parts.history,
                "text": parts.text,
                "drafting": grammar.sections.drafting,
                "node_id": n["id"],
                "dropped": parts.dropped,
                "page_first": doc["page_first"],
                "page_last": doc["page_last"],
                "line_first": min(indexes, default=n["line_first"]),
                "line_last": max(indexes, default=n["line_last"]),
            }
        )

    holding = {s["node_treepath"] for s in sections}
    heads = [n for n in nodes if n["kind"] == "division" and n["level"] != level]
    heads_out = [
        {
            "node_treepath": n["id"],
            "parent_treepath": n["parent_id"] or None,
            "level": n["level"],
            "number": n["number"],
            "heading": n["heading"],
            "depth": n["depth"],
            "position": n["position"],
            "sequence": i,
            "contains_sections": n["id"] in holding,
        }
        for i, n in enumerate(heads)
    ]
    return Carved(sections, heads_out)
