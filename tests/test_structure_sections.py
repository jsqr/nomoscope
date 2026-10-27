import pytest

from nomoscope.structure import checks, sections
from nomoscope.structure import grammar as gr
from nomoscope.structure.parse import parse
from tests.structure import GRAMMAR, RUN_IN, B, C, lines

RULES = gr.Sections(history=[r"^\(Ord\. "], drop=[r"^Editor's note"], drafting="numbered")


def test_split():
    text = "A. Permits.\nEditor's note: See chapter 9.\nB. Fees.\n(Ord. 1, 2020)\n(Ord. 2, 2021)"
    got = sections.split(text, RULES)
    assert got.text == "A. Permits.\nB. Fees."
    assert got.history == "(Ord. 1, 2020)\n(Ord. 2, 2021)"
    assert got.dropped == ["Editor's note: See chapter 9."]
    assert sections.split("(Ord. 1) is cited.\nText.", RULES).history is None


def test_tabulate():
    found = lines(
        ("Chapter 5", 280, C),
        ("FIRE", 280, C),
        ("ARTICLE I", 280, C),
        ("General", 280, C),
        ("§ 5-1. Purpose.", 72, B),
        ("The Town finds that", 72),
        ("fires happen.", 72, {"gap_before": 13.0}),
        ("A. Permits.", 72),
        ("Editor's note: Amended 2020.", 72),
        ("(Ord. 3, 2019)", 72),
        ("§ 5-2. Fees.", 72, B),
        ("Fees are set.", 72),
    )
    g = GRAMMAR.model_copy(update={"sections": RULES})
    tabulated = sections.tabulate(parse(found, g), found, g)
    first, second = tabulated.sections
    assert (first["section_num"], first["node_treepath"], first["heading"]) == (
        "5-1",
        "5/I",
        "Purpose.",
    )
    assert first["text"] == "The Town finds that fires happen.\nA. Permits."
    assert first["history"] == "(Ord. 3, 2019)"
    assert first["dropped"] == ["Editor's note: Amended 2020."]
    assert first["drafting"] == "numbered"
    assert (first["line_first"], first["line_last"]) == (5, 9)
    assert second["text"] == "Fees are set." and second["history"] is None
    assert [
        (n["node_treepath"], n["parent_treepath"], n["contains_sections"]) for n in tabulated.nodes
    ] == [
        ("5", None, False),
        ("5/I", "5", True),
    ]
    assert [n["level"] for n in tabulated.nodes] == ["chapter", "article"]


def test_subdivision_spans_index_the_normalized_text():
    found = lines(
        ("§ 5-1. Purpose.", 72, B),
        ("The Town finds that", 72),
        ("A. “Permits”  are due.", 72),
        ("(1) Fees.", 96),
        ("(2) Terms are set", 96),
        ("by resolution.", 96, {"gap_before": 13.0}),
        ("Editor's note: Amended 2020.", 72),
        ("B. (1) Stacked first.", 72),
        ("(2) Stacked second.", 96),
        ("(Ord. 3, 2019)", 72),
    )
    g = GRAMMAR.model_copy(update={"sections": RULES})
    [section] = sections.tabulate(parse(found, g), found, g).sections
    text = section["text"]
    assert text == (
        'The Town finds that\nA. "Permits" are due.\n(1) Fees.\n'
        "(2) Terms are set by resolution.\nB. (1) Stacked first.\n(2) Stacked second."
    )
    cut = {s["path"]: text[s["start_char"] : s["end_char"]] for s in section["subdivisions"]}
    assert cut == {
        "A": 'A. "Permits" are due.\n(1) Fees.\n(2) Terms are set by resolution.',
        "A/1": "(1) Fees.",
        "A/2": "(2) Terms are set by resolution.",
        "B": "B. (1) Stacked first.\n(2) Stacked second.",
        "B/1": "(1) Stacked first.",
        "B/2": "(2) Stacked second.",
    }
    levels = {s["path"]: (s["level"], s["style"], s["number"]) for s in section["subdivisions"]}
    assert levels["A"] == ("subsection", "upper_alpha", "A")
    assert levels["B/2"] == ("paragraph", "arabic", "2")


def test_a_dropped_subdivision_leaves_with_its_children():
    rules = gr.Sections(drop=[r"^B\. Repealed"])
    found = lines(
        ("§ 5-1. Purpose.", 72, B),
        ("A. Kept.", 72),
        ("B. Repealed.", 72),
        ("C. Kept too.", 72),
    )
    g = GRAMMAR.model_copy(update={"sections": rules})
    [section] = sections.tabulate(parse(found, g), found, g).sections
    assert section["text"] == "A. Kept.\nC. Kept too."
    assert [s["path"] for s in section["subdivisions"]] == ["A", "C"]


def test_run_in_terms_are_subdivisions_by_slug():
    found = lines(
        ("§ 1. Definitions.", 72, B),
        ("BUILDING — A structure.", 72),
        ("A. It has a roof.", 96),
        ("STREET — A road.", 72),
    )
    [section] = sections.tabulate(parse(found, RUN_IN), found, RUN_IN).sections
    cut = {
        s["path"]: section["text"][s["start_char"] : s["end_char"]] for s in section["subdivisions"]
    }
    assert cut == {
        "building": "BUILDING — A structure.\nA. It has a roof.",
        "building/A": "A. It has a roof.",
        "street": "STREET — A road.",
    }
    assert {s["level"] for s in section["subdivisions"]} == {"run_in", "subsection"}


DEFINITIONS = lines(
    ("Chapter 5", 280, C),
    ("FIRE", 280, C),
    ("§ 5-1. Purpose.", 72, B),
    ("Text.", 72),
    ("Chapter 6", 280, C),
    ("DEFINITIONS", 280, C),
    ("Words have these meanings.", 72),
    ("A. Building means a structure.", 72),
    ("B. Street means a road.", 72),
)


def test_an_undivided_chapter_is_tabulated_when_its_level_is_named():
    g = GRAMMAR.model_copy(update={"sections": gr.Sections(levels=["chapter"])})
    tabulated = sections.tabulate(parse(DEFINITIONS, g), DEFINITIONS, g)
    by_num = {s["section_num"]: s for s in tabulated.sections}
    assert set(by_num) == {"5-1", "6"}
    six = by_num["6"]
    assert (six["heading"], six["node_treepath"]) == ("DEFINITIONS", None)
    assert six["text"].startswith("Words have these meanings.\nA. Building")
    assert [s["path"] for s in six["subdivisions"]] == ["A", "B"]
    assert [n["node_treepath"] for n in tabulated.nodes] == ["5"]


def test_an_undivided_chapter_is_a_heading_and_a_finding_otherwise():
    tabulated = sections.tabulate(parse(DEFINITIONS, GRAMMAR), DEFINITIONS, GRAMMAR)
    assert {s["section_num"] for s in tabulated.sections} == {"5-1"}
    assert [n["node_treepath"] for n in tabulated.nodes] == ["5", "6"]
    found = checks.unsectioned(parse(DEFINITIONS, GRAMMAR), GRAMMAR)
    assert [(f["node_id"], f["detail"]) for f in found] == [
        ("6", "no section under it: 2 subdivisions, 26 characters of text")
    ]
    named = GRAMMAR.model_copy(update={"sections": gr.Sections(levels=["chapter"])})
    assert checks.unsectioned(parse(DEFINITIONS, named), named) == []


def test_sections_levels_must_be_division_levels():
    with pytest.raises(ValueError, match="not division levels"):
        GRAMMAR.model_validate({**GRAMMAR.model_dump(), "sections": {"levels": ["subsection"]}})


def test_unnumbered_divisions_are_not_unsectioned():
    marks = [{"ordinal": 0, "depth": 0, "title": "Preface", "page": 0, "top": 60.0}]
    found = lines(("PREFACE", 280, C), *[("This code is republished. " * 3, 72)] * 4)
    nodes = parse(found, GRAMMAR, marks)
    assert [n["level"] for n in nodes] == ["document", "heading"]
    assert checks.unsectioned(nodes, GRAMMAR) == []


TAIL = gr.Sections(
    history=[r"^\(Ord\. "], history_tail=[r"\s+\(Ord\. [^()]*(?:\([^()]*\)[^()]*)*\)$"]
)


def test_history_at_the_end_of_the_last_paragraph():
    got = sections.split("A. Seal.\nB. The clerk keeps it.  (Ord. 77-01 (part), 1977)", TAIL)
    assert got.text == "A. Seal.\nB. The clerk keeps it."
    assert got.history == "(Ord. 77-01 (part), 1977)"
    cited = sections.split("A. Under (Ord. 5) the clerk keeps it.", TAIL)
    assert cited.history is None
    both = sections.split("Text (Ord. 5) here. (Ord. 6, 1990)", TAIL)
    assert (both.text, both.history) == ("Text (Ord. 5) here.", "(Ord. 6, 1990)")


def test_a_tail_note_shortens_the_last_subdivision():
    found = lines(
        ("§ 5-1. Purpose.", 72, B),
        ("A. First.", 72),
        ("B. The clerk keeps it.  (Ord. 77-01 (part), 1977)", 72),
    )
    g = GRAMMAR.model_copy(update={"sections": TAIL})
    [section] = sections.tabulate(parse(found, g), found, g).sections
    assert section["history"] == "(Ord. 77-01 (part), 1977)"
    b = next(s for s in section["subdivisions"] if s["path"] == "B")
    assert section["text"][b["start_char"] : b["end_char"]] == "B. The clerk keeps it."


NOTES = gr.Sections(
    notes=[r"\[(?:Adopted|Amended|HISTORY:)[^\]]*\]"], inherit=True, drafting="bracketed"
)


def test_notes_stay_in_the_text_and_a_section_without_them_inherits():
    found = lines(
        ("Chapter 5", 280, C),
        ("FIRE", 280, C),
        ("[HISTORY: Adopted 2-15-2018 by L.L. No. 1-2018.]", 72),
        ("ARTICLE I", 280, C),
        ("General", 280, C),
        ("[Adopted 4-18-1991 by L.L. No. 1-1991]", 72),
        ("§ 5-1. Purpose.", 72, B),
        ("A. Permits. [Amended 6-16-2005 by L.L. No. 2-2005]", 72),
        ("B. Fees.", 72),
        ("§ 5-2. Fees.", 72, B),
        ("Fees are set.", 72),
    )
    g = GRAMMAR.model_copy(update={"sections": NOTES})
    first, second = sections.tabulate(parse(found, g), found, g).sections
    assert first["text"] == "A. Permits. [Amended 6-16-2005 by L.L. No. 2-2005]\nB. Fees."
    assert first["history"] == "[Amended 6-16-2005 by L.L. No. 2-2005]"
    assert first["division_history"] is None
    assert second["history"] is None
    assert second["division_history"] == "[Adopted 4-18-1991 by L.L. No. 1-1991]"
    plain = GRAMMAR.model_copy(update={"sections": NOTES.model_copy(update={"inherit": False})})
    assert (
        sections.tabulate(parse(found, plain), found, plain).sections[1]["division_history"] is None
    )
