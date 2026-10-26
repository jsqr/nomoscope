"""Parse a document's lines into a tree of nodes, as a grammar directs.

Divisions (chapters, articles, sections) come from bookmarks where the
document has them, and otherwise from lines that start with a division
label. Where the bookmarks stop above the deepest division level, the
lower levels come from labels, and a label must agree with the
bookmarked divisions it falls in.

A subdivision label continues the deepest open sequence it follows,
else opens a first child, else replaces the open node of its level. A
run-in paragraph, such as a defined term, opens a node that holds the
items after it. Other lines are text of the deepest open node. Text
before the first division belongs to the root (id "").

A table of contents parses as childless divisions that recur later.
`fold_tocs` moves them into their parent's `toc`.
"""

import re
from dataclasses import dataclass, replace
from typing import Any

from nomoscope.structure import Row
from nomoscope.structure import bookmarks as bm
from nomoscope.structure import enumerate as en
from nomoscope.structure import grammar as gr

HEADING_LINES = 3
# A paragraph break is a gap this many line heights or more.
PARAGRAPH_GAP = 1.5
BOLD = 0.5
INDENT_SLACK = 2.0
# Subdivisions rank below every division, however deep the bookmarks go.
SUBDIVISION_RANK = 1000
RUN_IN = "run_in"


@dataclass(frozen=True)
class _Open:
    row: Row
    rank: int
    enum: en.Enumerator | None


def holds(signals: gr.Signals, line: Row) -> bool:
    """Whether `line` has every feature `signals` requires. A feature the line lacks passes."""

    def flag(want: bool | None, have) -> bool:
        if want is None or have is None:
            return True
        return want == (have >= BOLD if isinstance(have, float) else bool(have))

    size, x, gap = line.get("size"), line.get("x0"), line.get("gap_before")
    return (
        flag(signals.bold, line.get("bold"))
        and flag(signals.italic, line.get("italic"))
        and flag(signals.caps, line.get("caps"))
        and flag(signals.centered, line.get("centered"))
        and (signals.size_min is None or size is None or size >= signals.size_min - 0.1)
        and (signals.size_max is None or size is None or size <= signals.size_max + 0.1)
        and (signals.x is None or x is None or abs(x - signals.x) <= signals.x_tolerance)
        and (signals.gap_min is None or gap is None or gap >= signals.gap_min)
    )


def agrees(m: gr.Match, stack: list[_Open]) -> bool:
    """Whether the enclosing numbers in a label match the open bookmarked divisions.

    This keeps a label that names another chapter from opening a division.
    """
    return all(
        en.same_number(o.row["number"], value)
        for level, value in m.refs.items()
        for o in stack
        if o.row["origin"] == "bookmark" and o.row["level"] == level
    )


def running(skip: gr.Skip, line: Row) -> bool:
    if line.get("block_type") in skip.block_types:
        return True
    y = line.get("y")
    if y is None:
        return False
    if skip.top is not None and y < skip.top:
        return True
    return skip.bottom is not None and y > skip.bottom


def _small(skip: gr.Skip, line: Row) -> bool:
    size = line.get("size")
    return skip.note_size_max is not None and size is not None and size <= skip.note_size_max


def _choose_subdivision(stack: list[_Open], found: list[gr.Match], floor: int):
    """Return the match to take and how many stack frames stay open under it."""
    for depth in range(len(stack) - 1, floor - 1, -1):
        for m in found:
            enum = stack[depth].enum
            if stack[depth].row["level"] == m.level.name and enum and en.succeeds(enum, m.enum):
                return m, depth
    for m in found:
        if m.enum.first:
            top = stack[-1]
            if len(stack) > floor and top.row["level"] == m.level.name:
                return m, len(stack) - 1
            return m, len(stack)
    for depth in range(len(stack) - 1, floor - 1, -1):
        for m in found:
            if stack[depth].row["level"] == m.level.name:
                return m, depth
    return found[0], len(stack)


def stacked(
    ms: list[gr.Matcher], ranks: dict[str, int], outer: gr.Match, rest: str
) -> gr.Match | None:
    """Return a first label of a deeper level that starts `rest`, as 1. does in "A. 1. Text".

    The label must be followed by a space, so "A. 1.5 acres" opens no paragraph.
    """
    for m in ms:
        if m.level.kind != "subdivision" or ranks[m.level.name] <= ranks[outer.level.name]:
            continue
        hit = gr.match(m, rest)
        if hit and hit.enum.first and rest[len(hit.label) : len(hit.label) + 1].isspace():
            return hit
    return None


def _node(parent: _Open, line: Row, index: int, **fields) -> Row:
    """Return a new node. Its id is the line index until `_renumber` assigns its path."""
    return {
        "id": f"#{index}",
        "parent_id": parent.row["id"],
        "origin": None,
        "bookmark": None,
        "level": None,
        "kind": None,
        "style": None,
        "number": None,
        "ordinal": None,
        "sub": None,
        "label": None,
        "heading": None,
        "depth": parent.row["depth"] + 1,
        "position": 0,
        "page_first": line["page"],
        "line_first": index,
        "text": "",
        "notes": [],
        "toc": [],
        "refs": [],
        "pieces": [],
        **fields,
    }


def _root(page: int) -> Row:
    """Return the root node, id ""."""
    above = _Open({"id": None, "depth": -1}, -1, None)
    return {
        **_node(above, {"page": page}, 0),
        "id": "",
        "origin": "root",
        "level": "document",
        "kind": "root",
    }


def _new(m: gr.Match, parent: _Open, line: Row, index: int) -> Row:
    return _node(
        parent,
        line,
        index,
        origin="label" if m.level.kind == "division" else "subdivision",
        level=m.level.name,
        kind=m.level.kind,
        style=m.enum.style,
        number=m.number,
        ordinal=m.enum.ordinal,
        sub=m.enum.sub,
        label=m.label.strip(),
        refs=[{"level": k, "value": v} for k, v in m.refs.items()],
    )


def _from_bookmark(a: bm.Anchored, loose: dict[str, gr.Matcher], parent: _Open, line: Row) -> Row:
    """Return a division node for an anchored bookmark.

    Its number is parsed with its level's template where the title fits it.
    """
    e = a.entry
    m = gr.match(loose[e.level], e.title.replace("\xa0", " ")) if e.level in loose else None
    return _node(
        parent,
        line,
        a.line,
        origin="bookmark",
        bookmark=e.ordinal,
        level=e.level,
        kind="division",
        style=m.enum.style if m else None,
        number=e.number,
        ordinal=m.enum.ordinal if m else None,
        sub=m.enum.sub if m else None,
        label=e.label,
        heading=e.heading or None,
        refs=[{"level": k, "value": v} for k, v in m.refs.items()] if m else [],
    )


def _own(row: Row, index: int, text: str, piece: str) -> None:
    """Record that `row` owns `piece`, the end of line `index`'s text."""
    if piece:
        row["pieces"].append({"line": index, "start": len(text) - len(piece)})


def _append(row: Row, line: Row, text: str) -> None:
    if not text:
        return
    if not row["text"]:
        row["text"] = text
        return
    row["text"] += ("\n" if starts_paragraph(line) else " ") + text


def starts_paragraph(line: Row) -> bool:
    """Whether a paragraph gap comes before `line`."""
    gap, size = line.get("gap_before"), line.get("size")
    return gap is not None and size is not None and gap >= PARAGRAPH_GAP * size


def _style(line: Row) -> tuple[Any, ...]:
    return ((line.get("bold") or 0) >= BOLD, bool(line.get("centered")))


def _continues_heading(node: Row, first: Row | None, line: Row) -> bool:
    """Whether `line` continues `node`'s heading.

    The first line is the heading. A later line joins it if it has the same
    style and follows closely.
    """
    if first is None:
        return node["heading"] is None
    if node["heading"] is None:
        return False
    if len(node["heading"].splitlines()) >= HEADING_LINES or _style(line) != _style(first):
        return False
    gap, size = line.get("gap_before"), line.get("size")
    return gap is not None and size is not None and gap < PARAGRAPH_GAP * size


def level_name(word: str, grammar: gr.Grammar) -> str:
    """Return the grammar level a bookmark's level word names: "ch" names "chapter"."""
    names = [lv.name for lv in grammar.levels]
    if word in names or len(word) < 2:
        return word
    starts = [n for n in names if n.startswith(word)]
    return starts[0] if len(starts) == 1 else word


def _opens_paragraph(line: Row) -> bool:
    gap, size = line.get("gap_before"), line.get("size")
    return gap is None or size is None or gap >= PARAGRAPH_GAP * size


def _indented(first: Row, line: Row) -> bool:
    """Whether the node starting at `first` is indented at or past `line`.

    A run-in paragraph closes such nodes and nests in the others.
    """
    x, at = first.get("x0"), line.get("x0")
    return x is None or at is None or x >= at - INDENT_SLACK


def _within(within: re.Pattern[str] | None, stack: list[_Open]) -> bool:
    """Whether an open division's heading matches `within`."""
    return within is not None and any(
        o.row["kind"] == "division" and o.row["heading"] and within.search(o.row["heading"])
        for o in stack
    )


def _lead(forms: list[tuple[gr.Form, re.Pattern[str]]], line: Row, text: str) -> gr.Lead | None:
    """Return the first form's phrase at the start of `text`."""
    raw = line.get("text") or ""
    cut = len(raw) - len(raw.lstrip())
    spans = [
        {"start": r["start"] - cut, "end": r["end"] - cut} for r in line.get("bold_spans") or []
    ]
    for form, regex in forms:
        if found := gr.match_lead(form, regex, text, 0, spans):
            return found
    return None


def anchors(lines: list[Row], grammar: gr.Grammar, marks: list[Row]) -> list[bm.Anchored]:
    """Anchor the bookmarks to lines, with level words mapped to the grammar's names.

    Returns none when `grammar.bookmarks` is false.
    """
    if not marks or not grammar.bookmarks:
        return []
    found = bm.anchor(bm.entries(marks), lines, lambda line: running(grammar.skip, line))
    return [
        replace(a, entry=replace(a.entry, level=level_name(a.entry.level, grammar))) for a in found
    ]


def labeled(grammar: gr.Grammar, anchored: list[bm.Anchored]) -> list[str]:
    """Return the division levels taken from labels.

    Without bookmarks, every division level; with them, the levels below the
    deepest bookmarked one.
    """
    divisions = [lv.name for lv in grammar.levels if lv.kind == "division"]
    if not anchored:
        return divisions
    named = {a.entry.level for a in anchored}
    covered = [i for i, name in enumerate(divisions) if name in named]
    return divisions[max(covered) + 1 :] if covered else []


def parse(lines: list[Row], grammar: gr.Grammar, marks: list[Row] | None = None) -> list[Row]:
    """Parse `lines`, in reading order, into nodes in document order.

    `marks` are the outline rows. With them, and with `grammar.bookmarks`
    set, divisions come from the bookmarks.
    """
    ms = gr.matchers(grammar)
    anchored = anchors(lines, grammar, marks or [])
    events = {a.line: a for a in anchored}
    below = max((a.entry.depth for a in anchored), default=-1) + 1
    ranks = {lv.name: below + i for i, lv in enumerate(grammar.levels)}
    divisions = [m for m in ms if m.level.kind == "division"]
    from_labels = labeled(grammar, anchored)
    ms = [m for m in ms if m.level.kind == "subdivision" or m.level.name in from_labels]
    loose = {m.level.name: m for m in gr.matchers(grammar, loose=True)}
    run_in = grammar.run_in
    within = re.compile(run_in.within) if run_in else None
    forms = [(f, gr.compile_form(f)) for f in run_in.forms] if run_in else []
    root = _root(lines[0]["page"] if lines else 0)
    rows = [root]
    stack = [_Open(root, -1, None)]
    heading: tuple[Row, Row | None] | None = None  # node, its first heading line
    consumed = 0  # heading lines still to skip after a bookmark's anchor line

    for index, line in enumerate(lines):
        text = (line.get("text") or "").strip()
        if not text or running(grammar.skip, line):
            continue
        if index in events:
            a = events[index]
            while len(stack) > 1 and stack[-1].rank >= a.entry.depth:
                stack.pop()
            row = _from_bookmark(a, loose, stack[-1], line)
            rows.append(row)
            stack.append(_Open(row, a.entry.depth, None))
            _append(row, line, a.rest)
            _own(row, index, text, a.rest)
            consumed = a.heading_lines - 1
            heading = None
            continue
        if consumed:
            consumed -= 1
            continue
        top = stack[-1].row
        block = line.get("block_type")
        if block in grammar.skip.note_block_types or _small(grammar.skip, line):
            top["notes"].append(text)
            continue
        if block in grammar.skip.toc_block_types:
            top["toc"].extend(_listed(divisions, text))
            _append(top, line, text)
            _own(top, index, text, text)
            continue

        found = [
            hit
            for m in ms
            if (hit := gr.match(m, text)) is not None
            and holds(m.level.signals, line)
            and agrees(hit, stack)
        ]
        if heading is not None:
            node, first = heading
            if not found and _continues_heading(node, first, line):
                if node["heading"] is None:
                    heading = (node, line)
                node["heading"] = f"{node['heading']}\n{text}" if node["heading"] else text
                continue
            heading = None

        division = next((m for m in found if m.level.kind == "division"), None)
        if division is not None:
            rank = ranks[division.level.name]
            while len(stack) > 1 and stack[-1].rank >= rank:
                stack.pop()
            row = _new(division, stack[-1], line, index)
            rows.append(row)
            stack.append(_Open(row, rank, division.enum))
            rest = division.rest.strip()
            if division.level.heading == "same_line":
                row["heading"] = rest or None
                heading = (row, line)
            elif division.level.heading == "next_line":
                _append(row, line, rest)
                _own(row, index, text, rest)
                heading = (row, None)
            else:
                _append(row, line, rest)
                _own(row, index, text, rest)
            continue

        subs = [m for m in found if m.level.kind == "subdivision"]
        if subs:
            floor = max(i for i, o in enumerate(stack) if o.row["kind"] != "subdivision") + 1
            m, keep = _choose_subdivision(stack, subs, floor)
            del stack[keep:]
            row = _new(m, stack[-1], line, index)
            rows.append(row)
            stack.append(_Open(row, SUBDIVISION_RANK + ranks[m.level.name], m.enum))
            # The document keeps a subdivision's label; the node's text does not.
            _own(row, index, text, text)
            rest = m.rest.strip()
            inside = 0
            while inner := stacked(ms, ranks, m, rest):
                m, inside = inner, inside + 1
                # A temporary id names one node, and this line has opened one already.
                row = {**_new(m, stack[-1], line, index), "id": f"#{index}.{inside}"}
                rows.append(row)
                stack.append(_Open(row, SUBDIVISION_RANK + ranks[m.level.name], m.enum))
                _own(row, index, text, rest)
                rest = m.rest.strip()
            _append(row, line, rest)
            continue

        if _within(within, stack) and _opens_paragraph(line) and (lead := _lead(forms, line, text)):
            floor = max(i for i, o in enumerate(stack) if o.row["kind"] != "subdivision") + 1
            while len(stack) > floor and _indented(lines[stack[-1].row["line_first"]], line):
                stack.pop()
            row = _node(
                stack[-1], line, index, origin=RUN_IN, level=RUN_IN,
                kind="subdivision", label=text[: lead.end].strip(),
            )  # fmt: skip
            rows.append(row)
            stack.append(_Open(row, SUBDIVISION_RANK - 1, None))
            _append(row, line, text[lead.end :].strip())
            _own(row, index, text, text)
            continue

        _append(top, line, text)
        _own(top, index, text, text)

    rows = _renumber(fold_tocs(rows, divisions) if from_labels else rows)
    _close(rows, lines)
    for row in rows:
        row["heading"] = row["heading"].replace("\n", " ") if row["heading"] else None
    return rows


def scope(node: Row, by_id: dict[str, Row]) -> str | None:
    """Return the node through which this node's level is numbered.

    That is the enclosing level the label names, as a section names its
    chapter, else the parent.
    """
    if not node["refs"]:
        return node["parent_id"]
    want = node["refs"][0]["level"]
    anc = by_id.get(node["parent_id"])
    while anc is not None and anc["level"] != want:
        anc = by_id.get(anc["parent_id"])
    return anc["id"] if anc is not None else node["parent_id"]


def _listed(divisions: list[gr.Matcher], text: str) -> list[Row]:
    return [
        {"level": m.level.name, "number": num} for m in divisions for num in gr.numbers(m, text)
    ]


def _key(r: Row, by_id: dict[str, Row]) -> tuple[Any, ...]:
    """Return a division's number within its scope, keyed by level and number.

    Node ids differ between a table of contents and the text it lists, so
    the key does not use them.
    """
    if r["refs"]:
        where = tuple((ref["level"], ref["value"]) for ref in r["refs"])
    else:
        key = scope(r, by_id)
        s = by_id.get(key) if key is not None else None
        where = (s["level"], s["number"]) if s else None
    return where, r["level"], r["number"]


def fold_tocs(rows: list[Row], divisions: list[gr.Matcher]) -> list[Row]:
    """Fold table-of-contents entries into their parent until none is left.

    An entry is a childless division whose number recurs later in its
    scope. Folding can leave a chapter childless, so the pass repeats.
    """
    while True:
        kept = _fold_once(rows, divisions)
        if len(kept) == len(rows):
            return kept
        rows = kept


def _fold_once(rows: list[Row], divisions: list[gr.Matcher]) -> list[Row]:
    by_id = {r["id"]: r for r in rows}
    parents = {r["parent_id"] for r in rows}
    later: dict[tuple[Any, ...], int] = {}
    for i, r in enumerate(rows):
        if r["kind"] == "division":
            later[_key(r, by_id)] = i
    keep = []
    for i, r in enumerate(rows):
        entry = r["origin"] == "label" and r["id"] not in parents
        if entry and later.get(_key(r, by_id), i) > i:
            parent = by_id[r["parent_id"]]
            parent["toc"].append({"level": r["level"], "number": r["number"]})
            parent["toc"].extend(_listed(divisions, f"{r['heading'] or ''} {r['text']}"))
            if r["text"]:
                parent["text"] = f"{parent['text']}\n{r['text']}" if parent["text"] else r["text"]
            parent["pieces"] = sorted(parent["pieces"] + r["pieces"], key=lambda p: p["line"])
            continue
        keep.append(r)
    return keep


def _slug(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")


def _renumber(rows: list[Row]) -> list[Row]:
    """Assign ids and positions after folding."""
    new_id: dict[str, str] = {"": ""}
    seen: dict[str, dict[str, int]] = {}
    for r in rows:
        if r["kind"] == "root":
            continue
        parent = new_id[r["parent_id"]]
        counts = seen.setdefault(parent, {})
        base = (_slug(r["label"]) if r["origin"] == RUN_IN else r["number"]) or "?"
        counts[base] = counts.get(base, 0) + 1
        key = base if counts[base] == 1 else f"{base}~{counts[base]}"
        new_id[r["id"]] = f"{parent}/{key}" if parent else key
        r["position"] = sum(counts.values()) - 1
        r["parent_id"] = parent
        r["id"] = new_id[r["id"]]
    return rows


def _close(rows: list[Row], lines: list[Row]) -> None:
    """Set each node's last line and page."""
    last = len(lines) - 1
    stack: list[Row] = []
    for row in reversed(rows):
        while stack and stack[-1]["depth"] > row["depth"]:
            stack.pop()
        end = stack[-1]["line_first"] - 1 if stack else last
        row["line_last"] = max(end, row["line_first"])
        row["page_last"] = lines[row["line_last"]]["page"] if lines else row["page_first"]
        stack.append(row)
