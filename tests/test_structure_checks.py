import polars as pl

from nomoscope.structure import checks
from nomoscope.structure import grammar as gr
from nomoscope.structure.parse import parse
from tests.structure import GRAMMAR as PARSE_GRAMMAR
from tests.structure import RUN_IN, B, lines

GRAMMAR = gr.Grammar(
    levels=[
        gr.Level(name="chapter", kind="division", label="Chapter {n}", style="arabic",
                 sequence="increasing", heading="next_line"),
        gr.Level(name="section", kind="division", label="§ {chapter}-{n}.", style="arabic"),
        gr.Level(name="subsection", kind="subdivision", label="{n}.", style="upper_alpha"),
        gr.Level(name="paragraph", kind="subdivision", label="({n})", style="arabic"),
    ],
)  # fmt: skip


def node(id_, parent, level, kind, number, style, ordinal, page=0, **kw) -> dict:
    return {
        "id": id_, "parent_id": parent, "level": level, "kind": kind, "number": number,
        "style": style, "ordinal": ordinal, "sub": 0, "page_first": page, "heading": "H",
        "text": "", "refs": [], "toc": [], **kw,
    }  # fmt: skip


def sec(id_, parent, number, ordinal, chapter, **kw):
    return node(id_, parent, "section", "division", number, "arabic", ordinal,
                refs=[{"level": "chapter", "value": chapter}], **kw)  # fmt: skip


ROOT = {**node("", None, "document", "root", None, None, None), "heading": None}
NODES = [
    ROOT,
    node("5", "", "chapter", "division", "5", "arabic", 5),
    sec("5/5-1", "5", "5-1", 1, "5"),
    sec("5/5-3", "5", "5-3", 3, "5"),
    sec("5/6-4", "5", "6-4", 4, "6"),
    node("5/5-3/B", "5/5-3", "subsection", "subdivision", "B", "upper_alpha", 2),
    node("5/5-3/B/1", "5/5-3/B", "paragraph", "subdivision", "1", "arabic", 1),
    node("5/5-3/B/1~2", "5/5-3/B", "paragraph", "subdivision", "1", "arabic", 1),
    node("9", "", "chapter", "division", "9", "arabic", 9, page=4, heading=None),
]


def details(rows, check):
    return [(r["node_id"], r["detail"]) for r in rows if r["check"] == check]


def test_sequence():
    assert details(checks.sequence(NODES, GRAMMAR), "sequence") == [
        ("5/5-3", "gap: 5-1 -> 5-3"),
        ("5/5-3/B", "starts at B"),
        ("5/5-3/B/1~2", "repeat: 1 -> 1"),
    ]


def test_ancestry():
    assert details(checks.ancestry(NODES, GRAMMAR), "ancestry") == [
        ("5/6-4", "chapter 6 under chapter 5")
    ]


def test_headings_and_depth():
    assert details(checks.headings(NODES, GRAMMAR), "heading") == [("9", "missing")]
    assert details(checks.depth(NODES, GRAMMAR), "depth") == []


def test_outline():
    titles = ["chapter 5: Fire", "§ 5-1 Purpose.", "§ 5-2 Fees.", "chapter 9: Water"]
    marks = pl.DataFrame(
        {"ordinal": range(4), "depth": [0, 1, 1, 0], "title": titles, "page": [0, 0, 1, 3],
         "top": [0.0] * 4}
    )  # fmt: skip
    assert details(checks.outline(NODES, marks, GRAMMAR), "outline") == [
        (None, "bookmark not parsed: § 5-2 Fees. (p1)"),
        ("5/5-3", "no bookmark"),
        ("5/6-4", "no bookmark"),
    ]
    summary = checks.outline_summary(NODES, marks, GRAMMAR).sort("level")
    assert summary["recall"].to_list() == [1.0, 0.5]


def test_headings_ignore_subdivisions():
    # A model set heading = "same_line" on subdivisions, which have no heading.
    levels = [lv.model_copy(update={"heading": "same_line"}) for lv in GRAMMAR.levels]
    g = GRAMMAR.model_copy(update={"levels": levels})
    flagged = {r["node_id"] for r in checks.headings(NODES, g)}
    assert not any("/B" in n for n in flagged)


def test_anchors_and_labels_in_bookmark_mode():
    marks = pl.DataFrame(
        {"ordinal": [0, 1], "depth": [0, 1], "title": ["chapter 5: Fire", "§\xa05-1 Purpose."],
         "page": [0, 0], "top": [72.0, 120.0]}
    )  # fmt: skip
    nodes = [ROOT, {**NODES[1], "origin": "bookmark", "bookmark": 0}]
    found_lines = [
        {"page": 0, "y": 100.0, "text": "§ 5-1. Purpose."},
        {"page": 0, "y": 200.0, "text": "§ 5-7. Not bookmarked."},
    ]
    found = checks.run(nodes, marks, GRAMMAR, found_lines)
    assert details(found, "anchors") == [(None, "bookmark not anchored: §\xa05-1 Purpose. (p0)")]
    assert details(found, "labels") == [
        (None, "label without bookmark: § 5-1. Purpose. (p0)"),
        (None, "label without bookmark: § 5-7. Not bookmarked. (p0)"),
    ]
    assert details(found, "outline") == []


def test_depth_under_run_in():
    rows = parse(
        lines(
            ("§ 1. Definitions.", 72, B),
            ("BUILDING — A structure.", 72), ("A. It has a roof.", 96), ("(1) Flat.", 120),
        ),
        RUN_IN,
    )  # fmt: skip
    assert [r["id"] for r in rows] == ["", "1", "1/building", "1/building/A", "1/building/A/1"]
    assert checks.depth(rows, RUN_IN) == []


def test_outline_summary_counts_divisions_that_share_a_title():
    g = gr.Grammar(
        levels=[
            gr.Level(name="chapter", kind="division", label="Chapter {n}", style="arabic"),
            gr.Level(name="article", kind="division", label="Article {n}.", style="arabic"),
        ],
    )
    marks = pl.DataFrame(
        [
            {"ordinal": 0, "depth": 0, "title": "Chapter 1", "page": 0, "top": 0.0},
            {"ordinal": 1, "depth": 1, "title": "Article 1. General", "page": 0, "top": 0.0},
            {"ordinal": 2, "depth": 1, "title": "Article 1. General", "page": 0, "top": 0.0},
            {"ordinal": 3, "depth": 0, "title": "Chapter 2", "page": 3, "top": 0.0},
            {"ordinal": 4, "depth": 1, "title": "Article 1. General", "page": 3, "top": 0.0},
        ]
    )
    nodes = [
        ROOT,
        node("1", "", "chapter", "division", "1", "arabic", 1),
        node("1/1", "1", "article", "division", "1", "arabic", 1),
        node("2", "", "chapter", "division", "2", "arabic", 2, page=3),
        node("2/1", "2", "article", "division", "1", "arabic", 1, page=3),
    ]
    summary = checks.outline_summary(nodes, marks, g).filter(pl.col("level") == "article")
    assert summary.select("bookmarks", "nodes", "recall", "precision").row(0) == (2, 2, 1.0, 1.0)


def test_textless_pages():
    assert checks.textless([{"page": 0}, {"page": 2}], 3) == [
        {"check": "textless", "node_id": None, "level": None,
         "detail": "1 pages without text: 1"}
    ]  # fmt: skip
    assert checks.textless([{"page": 0}], 1) == []


def test_counts_and_worse():
    found = checks.sequence(NODES, GRAMMAR) + checks.headings(NODES, GRAMMAR)
    assert checks.counts(found) == {"heading": 1, "sequence": 3}
    assert checks.worse({"heading": 1, "sequence": 3}, {"sequence": 2}) == {
        "heading": 1,
        "sequence": 1,
    }
    assert checks.worse({"sequence": 1}, {"sequence": 2}) == {}


def test_scheme_reports_sections_off_the_usual_order():
    g = PARSE_GRAMMAR.model_copy(update={"sections": gr.Sections(level_names="depth")})
    rows = parse(
        lines(
            ("§ 5-1. One.", 72, B), ("A. First.", 72), ("(1) Item.", 96),
            ("§ 5-2. Two.", 72, B), ("A. First.", 72), ("(1) Item.", 96),
            ("§ 5-3. Short.", 72, B), ("A. Only.", 72),
            ("§ 5-4. Odd.", 72, B), ("(a) Sub.", 120), ("(1) Item.", 96),
        ),
        g,
    )  # fmt: skip
    found = checks.scheme(rows, g)
    assert [(f["node_id"], f["detail"]) for f in found] == [
        ("5-4", "lower_alpha > arabic; most sections upper_alpha > arabic")
    ]
    assert checks.scheme(rows, PARSE_GRAMMAR) == []


def test_lists_outside_carved_sections_are_not_checked():
    marks = [
        {"ordinal": 0, "depth": 0, "title": "Preface", "page": 0, "top": 60.0},
        {"ordinal": 1, "depth": 0, "title": "chapter 5: Fire", "page": 0, "top": 100.0},
    ]
    rows = parse(
        lines(
            ("PREFACE", 280), ("(2) Second item first.", 96),
            ("Chapter 5", 280), ("FIRE", 280),
            ("§ 5-1. Purpose.", 72, B), ("(2) Starts at two.", 96),
        ),
        PARSE_GRAMMAR,
        marks,
    )  # fmt: skip
    found = checks.sequence(rows, PARSE_GRAMMAR) + checks.depth(rows, PARSE_GRAMMAR)
    assert {f["node_id"] for f in found} == {"5/5-1/2"}


def test_hyphens_reports_kept_compounds():
    g = PARSE_GRAMMAR.model_copy(update={"sections": gr.Sections(hyphens="compounds")})
    near = {"gap_before": 13.0}
    found = lines(
        ("§ 5-1. Purpose.", 72, B), ("autho-", 72), ("rized, as authorized, on-", 72, near),
        ("site.", 72, near),
    )  # fmt: skip
    assert [f["detail"] for f in checks.hyphens(found, g)] == ["guessed on-site (p1)"]
    assert checks.hyphens(found, PARSE_GRAMMAR) == []


def test_a_correction_that_matches_no_line():
    found = lines(("A. First.", 72), ("b. Second.", 72))
    g = PARSE_GRAMMAR.model_copy(
        update={
            "corrections": [
                gr.Correction(page=1, printed="b. Second.", reads="B. Second."),
                gr.Correction(page=2, printed="b. Second.", reads="B. Second."),
            ]
        }
    )
    assert [f["detail"] for f in checks.corrections(found, g)] == ["no line: 'b. Second.' (p2)"]
