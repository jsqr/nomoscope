"""Turn a parsed tree into law_sections and law_nodes rows.

Sections are the divisions at the grammar's deepest division level, and
the divisions at a level in `[sections] levels` with no division under
them. Every other division is a heading row. A section's text is its document
(`documents.py`) without the paragraphs `[sections]` drops, and its
trailing history paragraphs go to `history`. The text is normalized
(`nomoscope.text`), and its subdivisions are spans into it. Treepaths are
node ids.
"""

import bisect
import re
from dataclasses import dataclass

from nomoscope.structure import Row, documents
from nomoscope.structure import grammar as gr
from nomoscope.text import normalize


@dataclass(frozen=True)
class Carved:
    sections: list[Row]
    nodes: list[Row]


def section_level(grammar: gr.Grammar) -> str | None:
    divisions = [lv.name for lv in grammar.levels if lv.kind == "division"]
    return divisions[-1] if divisions else None


def is_section(node: Row, grammar: gr.Grammar, divided: set[str]) -> bool:
    """Whether a node is carved as a section.

    `divided` holds the ids of the nodes with a division under them.
    """
    if node["kind"] != "division":
        return False
    if node["level"] == section_level(grammar):
        return True
    return node["level"] in grammar.sections.levels and node["id"] not in divided


@dataclass(frozen=True)
class Split:
    text: str
    history: str | None
    dropped: list[str]
    kept: list[int]


def split(text: str, rules: gr.Sections) -> Split:
    """Split a section's text into text, history, and dropped paragraphs."""
    drop = [re.compile(r) for r in rules.drop]
    history = [re.compile(r) for r in rules.history]
    kept: list[int] = []
    dropped: list[str] = []
    paras = text.split("\n")
    for i, para in enumerate(paras):
        if any(r.search(para) for r in drop):
            dropped.append(para)
        else:
            kept.append(i)
    note: list[str] = []
    while kept and any(r.search(paras[kept[-1]]) for r in history):
        note.insert(0, paras[kept.pop()])
    body = "\n".join(paras[i] for i in kept)
    return Split(body, "\n".join(note) or None, dropped, kept)


def _offsets(text: str) -> list[int]:
    starts = [0]
    for line in text.split("\n")[:-1]:
        starts.append(starts[-1] + len(line) + 1)
    return starts


def spans(doc: str, parts: Split, subs: list[Row], by_id: dict[str, Row]) -> list[Row]:
    """Subdivision spans into the section's normalized text.

    A subdivision whose line was dropped or moved to history is left out,
    with its descendants. A subdivision ends where the next one that is not
    its descendant starts, without trailing whitespace, as in
    `derive/subdivide.py`.
    """
    paras = doc.split("\n")
    old_starts = _offsets(doc)
    new_line = {old: new for new, old in enumerate(parts.kept)}
    text = normalize(parts.text) or ""
    new_starts = _offsets(text)
    out: list[Row] = []
    gone: list[str] = []
    for s in sorted(subs, key=lambda s: s["start_char"]):
        path = s["path"]
        old = bisect.bisect_right(old_starts, s["start_char"]) - 1
        if old not in new_line or any(path == g or path.startswith(f"{g}/") for g in gone):
            gone.append(path)
            continue
        prefix = paras[old][: s["start_char"] - old_starts[old]]
        # Normalizing the prefix with a character after it keeps its
        # trailing space, which normalize would otherwise trim.
        column = len(normalize(prefix + "x") or "") - 1
        node = by_id[s["node_id"]]
        out.append(
            {
                "path": path,
                "level": node["level"],
                "style": node["style"],
                "number": node["number"] or node["label"],
                "start_char": new_starts[new_line[old]] + column,
            }
        )
    for i, row in enumerate(out):
        after = (
            r["start_char"] for r in out[i + 1 :] if not r["path"].startswith(f"{row['path']}/")
        )
        end = next(after, len(text))
        while end > row["start_char"] and text[end - 1].isspace():
            end -= 1
        row["end_char"] = end
    return out


def carve(nodes: list[Row], lines: list[Row], grammar: gr.Grammar) -> Carved:
    divided = {n["parent_id"] for n in nodes if n["kind"] == "division"}
    docs, subs, back = documents.build(nodes, lines)
    by_doc = {d["node_id"]: d for d in docs}
    by_id = {n["id"]: n for n in nodes}
    subs_of: dict[str, list[Row]] = {}
    for sub in subs:
        if sub["start_char"] is not None:
            subs_of.setdefault(sub["document"], []).append(sub)
    lines_of: dict[str, list[int]] = {}
    for b in back:
        lines_of.setdefault(b["document"], []).append(b["index"])

    sections = []
    for n in nodes:
        if not is_section(n, grammar, divided):
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
                "text": normalize(parts.text),
                "subdivisions": spans(doc["text"], parts, subs_of.get(n["id"], []), by_id),
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
    heads = [n for n in nodes if n["kind"] == "division" and not is_section(n, grammar, divided)]
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
