"""Checks over a parsed tree.

Each check returns rows of `(check, node_id, level, detail)`. `node_id`
is null when no node is involved. `counts` tallies findings by check,
and `worse` compares the tally with the counts a review accepted.
"""

import itertools
from collections import Counter

import polars as pl

from nomoscope.structure import Row, documents, parse, sections
from nomoscope.structure import bookmarks as bm
from nomoscope.structure import enumerate as en
from nomoscope.structure import grammar as gr

SCHEMA = {
    "check": pl.String,
    "node_id": pl.String,
    "level": pl.String,
    "detail": pl.String,
}
LONG_HEADING = 200
# Own text at or above this, in characters, makes an undivided division law, not a note.
UNSECTIONED_TEXT = 200
PAGE_SLACK = 1


def _row(node: Row | None, check: str, detail: str, level=None) -> Row:
    return {
        "check": check,
        "node_id": node["id"] if node else None,
        "level": node["level"] if node else level,
        "detail": detail,
    }


def _enum(node: Row) -> en.Enumerator:
    return en.Enumerator(node["style"], node["ordinal"], node["sub"])


def uncarved(nodes: list[Row], grammar: gr.Grammar) -> set[str]:
    """The ids of subdivisions outside every carved section, such as a preface's list.

    The structure checks skip them: nothing of theirs reaches derive.
    """
    by_id = {n["id"]: n for n in nodes}
    divided = {n["parent_id"] for n in nodes if n["kind"] == "division"}
    out = set()
    for n in nodes:
        if n["kind"] != "subdivision":
            continue
        owner = n
        while owner["kind"] == "subdivision":
            owner = by_id[owner["parent_id"]]
        if not sections.is_section(owner, grammar, divided):
            out.add(n["id"])
    return out


def sequence(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Siblings that do not start at a first value or do not follow in order."""
    modes = {lv.name: lv.sequence for lv in grammar.levels}
    by_id = {n["id"]: n for n in nodes}
    skipped = uncarved(nodes, grammar)
    groups: dict[tuple[str | None, str], list[Row]] = {}
    for n in nodes:
        if n["id"] in skipped:
            continue
        # A bookmark whose title fits no template has no enumerator to follow.
        if n["kind"] != "root" and n["style"] is not None:
            groups.setdefault((parse.scope(n, by_id), n["level"]), []).append(n)
    out = []
    for (_, level), sibs in groups.items():
        mode = modes.get(level, "none")
        if mode == "none":
            continue
        if mode == "consecutive" and not _enum(sibs[0]).first:
            out.append(_row(sibs[0], "sequence", f"starts at {sibs[0]['number']}"))
        for prev, cur in itertools.pairwise(sibs):
            a, b = _enum(prev), _enum(cur)
            ok = en.succeeds(a, b) if mode == "consecutive" else en.increases(a, b)
            if ok:
                continue
            kind = (
                "repeat"
                if (a.ordinal, a.sub) == (b.ordinal, b.sub)
                else ("backward" if (b.ordinal, b.sub) < (a.ordinal, a.sub) else "gap")
            )
            out.append(_row(cur, "sequence", f"{kind}: {prev['number']} -> {cur['number']}"))
    return out


def ancestry(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Labels whose reference to an enclosing level names the wrong number."""
    by_id = {n["id"]: n for n in nodes}
    out = []
    for n in nodes:
        for ref in n["refs"]:
            anc = by_id.get(n["parent_id"])
            while anc is not None and anc["level"] != ref["level"]:
                anc = by_id.get(anc["parent_id"])
            if anc is None:
                detail = f"no enclosing {ref['level']} for {ref['value']}"
            elif not en.same_number(anc["number"], ref["value"]):
                detail = f"{ref['level']} {ref['value']} under {ref['level']} {anc['number']}"
            else:
                continue
            out.append(_row(n, "ancestry", detail))
    return out


def _bookmarks(outline: pl.DataFrame, grammar: gr.Grammar) -> pl.DataFrame:
    ms = [gr.compile(lv, loose=True) for lv in grammar.levels if lv.kind == "division"]
    rows = []
    # `entries` drops the repeated bookmarks of one division; two divisions
    # may still share a title, as "Article 1. General" in two chapters.
    for e in bm.entries(outline.to_dicts()):
        if hit := gr.classify(e.title, ms):
            rows.append(
                {"level": hit.level.name, "number": hit.number, "page": e.page, "title": e.title}
            )
    schema = {"level": pl.String, "number": pl.String, "page": pl.Int64, "title": pl.String}
    return pl.DataFrame(rows, schema=schema)


def _aligned(nodes: list[Row], outline: pl.DataFrame, grammar: gr.Grammar):
    marks = _bookmarks(outline, grammar).with_row_index("mark")
    divs = pl.DataFrame(
        [
            {
                "node_id": n["id"],
                "level": n["level"],
                "number": n["number"],
                "node_page": n["page_first"],
            }
            for n in nodes
            if n["kind"] == "division"
        ],
        schema={
            "node_id": pl.String,
            "level": pl.String,
            "number": pl.String,
            "node_page": pl.Int64,
        },
    )
    pairs = marks.join(divs, on=["level", "number"]).filter(
        (pl.col("node_page") - pl.col("page")).is_between(0, PAGE_SLACK)
    )
    return marks, divs, pairs


def outline(nodes: list[Row], marks: pl.DataFrame, grammar: gr.Grammar) -> list[Row]:
    """Division nodes and bookmarks that do not match by level, number, and page."""
    if marks.height == 0:
        return []
    bookmarks, divs, pairs = _aligned(nodes, marks, grammar)
    by_id = {n["id"]: n for n in nodes}
    out = [
        _row(None, "outline", f"bookmark not parsed: {m['title']} (p{m['page']})", m["level"])
        for m in bookmarks.join(pairs.select("mark"), on="mark", how="anti").iter_rows(named=True)
    ]
    levels = set(bookmarks["level"])
    for d in divs.join(pairs.select("node_id"), on="node_id", how="anti").iter_rows(named=True):
        if d["level"] in levels:
            out.append(_row(by_id[d["node_id"]], "outline", "no bookmark"))
    return out


def outline_summary(nodes: list[Row], marks: pl.DataFrame, grammar: gr.Grammar) -> pl.DataFrame:
    """Bookmark and node counts per division level, with recall and precision."""
    bookmarks, divs, pairs = _aligned(nodes, marks, grammar)
    return (
        bookmarks.group_by("level")
        .agg(bookmarks=pl.len())
        .join(divs.group_by("level").agg(nodes=pl.len()), on="level", how="full", coalesce=True)
        .join(
            pairs.group_by("level").agg(
                bookmarks_matched=pl.col("mark").n_unique(),
                nodes_matched=pl.col("node_id").n_unique(),
            ),
            on="level",
            how="left",
        )
        .with_columns(
            recall=(pl.col("bookmarks_matched") / pl.col("bookmarks")).round(3),
            precision=(pl.col("nodes_matched") / pl.col("nodes")).round(3),
        )
    )


def toc(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Differences between a node's table of contents and the divisions parsed under it."""
    out = []
    for n in nodes:
        if not n["toc"]:
            continue
        listed = {(e["level"], e["number"]) for e in n["toc"]}
        levels = {level for level, _ in listed}
        prefix = f"{n['id']}/" if n["id"] else ""
        below = {
            (d["level"], d["number"])
            for d in nodes
            if d["level"] in levels and d["id"].startswith(prefix)
        }
        for level, num in sorted(listed - below):
            out.append(_row(n, "toc", f"listed, not parsed: {level} {num}"))
        for level, num in sorted(below - listed):
            out.append(_row(n, "toc", f"parsed, not listed: {level} {num}"))
    return out


def coverage(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Text before the first division."""
    root = next(n for n in nodes if n["kind"] == "root")
    if not root["text"]:
        return []
    return [_row(root, "coverage", f"{len(root['text'])} characters before the first division")]


def headings(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    wants = {lv.name for lv in grammar.levels if lv.kind == "division" and lv.heading != "none"}
    out = []
    for n in nodes:
        if n["level"] in wants and not n["heading"]:
            out.append(_row(n, "heading", "missing"))
        elif n["heading"] and len(n["heading"]) > LONG_HEADING:
            out.append(_row(n, "heading", f"{len(n['heading'])} characters"))
    return out


def depth(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Subdivisions that skip a level below their parent."""
    subs = [lv.name for lv in grammar.levels if lv.kind == "subdivision"]
    rank = {name: i for i, name in enumerate(subs)}
    by_id = {n["id"]: n for n in nodes}
    out = []
    skipped = uncarved(nodes, grammar)
    for n in nodes:
        if n["kind"] != "subdivision" or n["level"] not in rank or n["id"] in skipped:
            continue
        parent = by_id[n["parent_id"]]
        # A run-in paragraph has no rank among the subdivision levels. Codes
        # number the items under one at any level: A. in one, (1) in another.
        if parent["kind"] == "subdivision" and parent["level"] not in rank:
            continue
        above = rank[parent["level"]] if parent["kind"] == "subdivision" else -1
        if rank[n["level"]] > above + 1:
            out.append(_row(n, "depth", f"{n['level']} under {parent['level']}"))
    return out


def anchors(nodes: list[Row], marks: pl.DataFrame, grammar: gr.Grammar) -> list[Row]:
    """Bookmarks that matched no line."""
    used = {n.get("bookmark") for n in nodes}
    return [
        _row(None, "anchors", f"bookmark not anchored: {e.title} (p{e.page})", e.level)
        for e in bm.entries(marks.to_dicts())
        if e.ordinal not in used
    ]


def labels(
    nodes: list[Row], grammar: gr.Grammar, lines: list[Row], marks: pl.DataFrame
) -> list[Row]:
    """Lines with a bookmarked level's label and a number no bookmark has.

    These may be headings the outline missed.
    """
    known = {n["number"] for n in nodes if n.get("origin") == "bookmark"}
    levels = {parse.level_name(e.level, grammar) for e in bm.entries(marks.to_dicts())}
    divisions = [
        m for m in gr.matchers(grammar) if m.level.kind == "division" and m.level.name in levels
    ]
    out = []
    for line in lines:
        text = (line.get("text") or "").strip()
        if not text or parse.running(grammar.skip, line):
            continue
        for m in divisions:
            hit = gr.match(m, text)
            if hit and parse.holds(m.level.signals, line) and hit.number not in known:
                out.append(
                    _row(None, "labels", f"label without bookmark: {text[:80]} (p{line['page']})",
                         m.level.name)
                )  # fmt: skip
                break
    return out


def unsectioned(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Numbered divisions above the section level with no division under them,
    but with subdivisions or text: law that is not carved, unless
    `[sections] levels` names the level.

    Unnumbered divisions, such as a preface or a disposition table, are
    front and back matter, not law.
    """
    level = sections.section_level(grammar)
    divided = {n["parent_id"] for n in nodes if n["kind"] == "division"}
    subs: dict[str, int] = {}
    for n in nodes:
        if n["kind"] == "subdivision":
            subs[n["parent_id"]] = subs.get(n["parent_id"], 0) + 1
    out = []
    for n in nodes:
        if n["kind"] != "division" or n["level"] == level or n["id"] in divided:
            continue
        if n["number"] is None:
            continue
        if n["level"] in grammar.sections.levels:
            continue
        count, size = subs.get(n["id"], 0), len(n["text"] or "")
        if count or size >= UNSECTIONED_TEXT:
            detail = f"no {level} under it: {count} subdivisions, {size} characters of text"
            out.append(_row(n, "unsectioned", detail))
    return out


def scheme(nodes: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Sections whose enumerator style at some depth is not the code's most
    common style at that depth, as "1." at the top where most sections start
    with "A.".

    Only where levels are named by depth; otherwise `depth` reports these.
    """
    if grammar.sections.level_names != "depth":
        return []
    by_id = {n["id"]: n for n in nodes}
    orders: dict[str, dict[int, str]] = {}
    skipped = uncarved(nodes, grammar)
    for n in nodes:
        if n["kind"] != "subdivision" or n["level"] == parse.RUN_IN or n["style"] is None:
            continue
        if n["id"] in skipped:
            continue
        depth, up = 1, by_id[n["parent_id"]]
        while up["kind"] == "subdivision":
            depth += up["level"] != parse.RUN_IN
            up = by_id[up["parent_id"]]
        orders.setdefault(up["id"], {}).setdefault(depth, n["style"])
    at_depth: dict[int, Counter[str]] = {}
    for found in orders.values():
        for d, style in found.items():
            at_depth.setdefault(d, Counter())[style] += 1
    usual = {d: c.most_common(1)[0][0] for d, c in at_depth.items()}
    order = " > ".join(usual[d] for d in sorted(usual))
    out = []
    for k, found in orders.items():
        if any(usual[d] != style for d, style in found.items()):
            chain = " > ".join(found[d] for d in sorted(found))
            out.append(_row(by_id[k], "scheme", f"{chain}; most sections {order}"))
    return out


def hyphens(lines: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Words broken at a line end that the document gives no evidence for, so the
    grammar's `hyphens` default decided whether to keep the hyphen.

    Only where the grammar rejoins broken words.
    """
    mode = grammar.sections.hyphens
    if mode == "keep":
        return []
    vocab = documents.vocabulary(lines)
    text = [(i, line) for i, line in enumerate(lines) if (line.get("text") or "").strip()]
    out = []
    for (_, a), (_, b) in itertools.pairwise(text):
        if parse.running(grammar.skip, a) or parse.running(grammar.skip, b):
            continue
        end, start = a["text"].strip(), b["text"].strip()
        joined = documents.rejoined(end, start, vocab, mode)
        if joined is not None and joined.guessed:
            word = documents.BROKEN.search(end)
            head = documents.CONTINUED.match(start)
            assert word is not None and head is not None
            made = f"{joined.text[word.start() :]}{head.group()}"
            out.append(_row(None, "hyphens", f"guessed {made} (p{b['page'] + 1})"))
    return out


def corrections(lines: list[Row], grammar: gr.Grammar) -> list[Row]:
    """Corrections that match no line, as when a new edition fixes the print."""
    out = []
    for c in grammar.corrections:
        if not any(
            line["page"] + 1 == c.page and (line.get("text") or "").strip().startswith(c.printed)
            for line in lines
        ):
            out.append(_row(None, "corrections", f"no line: {c.printed!r} (p{c.page})"))
    return out


def textless(lines: list[Row], pages: int) -> list[Row]:
    """Pages with no lines: blank, or scanned without a text layer."""
    have = {line["page"] for line in lines}
    missing = [p for p in range(pages) if p not in have]
    if not missing:
        return []
    shown = ", ".join(str(p) for p in missing[:20]) + (" ..." if len(missing) > 20 else "")
    return [_row(None, "textless", f"{len(missing)} pages without text: {shown}")]


def run(
    nodes: list[Row], marks: pl.DataFrame, grammar: gr.Grammar, lines: list[Row] | None = None
) -> list[Row]:
    """Run every check over one document's nodes.

    Divisions from bookmarks are checked with `anchors` and `labels`.
    Divisions from labels are checked against the bookmarks with
    `outline`, and against tables of contents with `toc`.
    """
    origins = {n.get("origin") for n in nodes}
    against = (
        [*anchors(nodes, marks, grammar), *labels(nodes, grammar, lines or [], marks)]
        if "bookmark" in origins
        else outline(nodes, marks, grammar)
    )
    if "label" in origins:
        against += toc(nodes, grammar)
    return [
        *sequence(nodes, grammar),
        *ancestry(nodes, grammar),
        *against,
        *coverage(nodes, grammar),
        *headings(nodes, grammar),
        *depth(nodes, grammar),
        *scheme(nodes, grammar),
        *unsectioned(nodes, grammar),
        *hyphens(lines or [], grammar),
        *corrections(lines or [], grammar),
    ]


def counts(findings: list[Row]) -> dict[str, int]:
    """Count findings by check."""
    return dict(sorted(Counter(f["check"] for f in findings).items()))


def worse(found: dict[str, int], accepted: dict[str, int]) -> dict[str, int]:
    """Return the checks with more findings than accepted, and the excess."""
    return {
        check: n - accepted.get(check, 0)
        for check, n in found.items()
        if n > accepted.get(check, 0)
    }
