from nomoscope.structure import grammar as gr
from nomoscope.structure import sections
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


def test_carve():
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
    carved = sections.carve(parse(found, g), found, g)
    first, second = carved.sections
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
        (n["node_treepath"], n["parent_treepath"], n["contains_sections"]) for n in carved.nodes
    ] == [
        ("5", None, False),
        ("5/I", "5", True),
    ]
    assert [n["level"] for n in carved.nodes] == ["chapter", "article"]


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
    [section] = sections.carve(parse(found, g), found, g).sections
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
    [section] = sections.carve(parse(found, g), found, g).sections
    assert section["text"] == "A. Kept.\nC. Kept too."
    assert [s["path"] for s in section["subdivisions"]] == ["A", "C"]


def test_run_in_terms_are_subdivisions_by_slug():
    found = lines(
        ("§ 1. Definitions.", 72, B),
        ("BUILDING — A structure.", 72),
        ("A. It has a roof.", 96),
        ("STREET — A road.", 72),
    )
    [section] = sections.carve(parse(found, RUN_IN), found, RUN_IN).sections
    cut = {
        s["path"]: section["text"][s["start_char"] : s["end_char"]] for s in section["subdivisions"]
    }
    assert cut == {
        "building": "BUILDING — A structure.\nA. It has a roof.",
        "building/A": "A. It has a roof.",
        "street": "STREET — A road.",
    }
    assert {s["level"] for s in section["subdivisions"]} == {"run_in", "subsection"}
