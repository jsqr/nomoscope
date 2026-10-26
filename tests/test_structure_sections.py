from nomoscope.structure import grammar as gr
from nomoscope.structure import sections
from nomoscope.structure.parse import parse
from tests.structure import GRAMMAR, B, C, lines

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
