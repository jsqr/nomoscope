import hashlib

from nomoscope.structure import documents
from nomoscope.structure import grammar as gr
from nomoscope.structure.parse import parse
from tests.structure import GRAMMAR, B, C, lines


def parsed():
    found = lines(
        ("Chapter 5", 280, C),
        ("FIRE", 280, C),
        ("[HISTORY: Adopted 1990.]", 72),
        ("§ 5-1. Purpose.", 72, B),
        ("The Town finds that", 72),
        ("fires happen.", 72, {"gap_before": 13.0}),
        ("A. Permits.", 72),
        ("(1) Fees.", 96),
        ("(2) Terms are set", 96),
        ("by resolution.", 96, {"gap_before": 13.0}),
        ("B. Inspections.", 72),
    )
    return parse(found, GRAMMAR), found


def test_documents_and_spans():
    nodes, found = parsed()
    docs, subs, back = documents.build(nodes, found)
    by_node = {d["node_id"]: d for d in docs}
    section = by_node["5/5-1"]
    assert section["citation"] == "5-1"
    assert section["heading"] == "Purpose."
    assert section["text"] == (
        "The Town finds that fires happen.\n"
        "A. Permits.\n(1) Fees.\n(2) Terms are set by resolution.\nB. Inspections."
    )
    assert section["text_sha256"] == hashlib.sha256(section["text"].encode()).hexdigest()
    assert by_node["5"]["text"] == "[HISTORY: Adopted 1990.]"
    assert by_node["5"]["citation"] == "5"

    cut = {s["path"]: section["text"][s["start_char"] : s["end_char"]] for s in subs}
    assert cut["A"] == "A. Permits.\n(1) Fees.\n(2) Terms are set by resolution."
    assert cut["A/2"] == "(2) Terms are set by resolution."
    assert cut["B"] == "B. Inspections."
    assert all(s["document"] == "5/5-1" for s in subs)

    rows = [b for b in back if b["document"] == "5/5-1"]
    assert [section["text"][b["start_char"] : b["end_char"]] for b in rows][:2] == [
        "The Town finds that",
        "fires happen.",
    ]
    assert {b["page"] for b in rows} == {0}
    assert rows[0]["index"] == 4


def section_text(found: list[dict]) -> str:
    docs, _, _ = documents.build(parse(found, GRAMMAR), found)
    return next(d["text"] for d in docs if d["citation"] == "5-1")


def test_stacked_labels_appear_once():
    found = lines(
        ("§ 5-1. Purpose.", 72, B),
        ("A. (1) Fees are due.", 72),
        ("(2) Fees are paid.", 96),
    )
    assert section_text(found) == "A. (1) Fees are due.\n(2) Fees are paid."


def test_a_subdivision_at_the_top_of_a_page_starts_a_line():
    body = [(f"Line {i} continues", 72, {"gap_before": 13.0}) for i in range(18)]
    found = lines(
        ("§ 5-1. Purpose.", 72, B),
        ("A. First.", 72),
        *body,
        ("B. Second.", 72, {"gap_before": None}),
    )
    assert found[-1]["page"] == 1
    text = section_text(found)
    assert text.startswith("A. First. Line 0 continues")
    assert text.endswith("Line 17 continues\nB. Second.")


def hyphen_text(*rows, mode: str = "compounds") -> str:
    g = GRAMMAR.model_copy(update={"sections": gr.Sections(hyphens=mode)})
    found = lines(("§ 5-1. Purpose.", 72, B), *rows)
    docs, _, back = documents.build(parse(found, g), found, mode)
    section = next(d for d in docs if d["citation"] == "5-1")
    for b in back:
        if b["document"] == section["node_id"]:
            assert section["text"][b["start_char"] : b["end_char"]].strip()
    return section["text"]


def test_rejoin_drops_a_syllable_hyphen_and_keeps_a_compound():
    near = {"gap_before": 13.0}
    text = hyphen_text(
        ("The permit is autho-", 72),
        ("rized by the clerk on-", 72, near),
        ("site, as authorized.", 72, near),
    )
    assert text == "The permit is authorized by the clerk on-site, as authorized."


def test_rejoin_leaves_a_new_subdivision_alone():
    text = hyphen_text(("A. A pre-", 72), ("(1) item.", 96))
    assert text == "A. A pre-\n(1) item."


def test_keep_leaves_broken_words_as_printed():
    found = lines(("§ 5-1. Purpose.", 72, B), ("autho-", 72), ("rized.", 72, {"gap_before": 13.0}))
    docs, _, _ = documents.build(parse(found, GRAMMAR), found)
    assert next(d for d in docs if d["citation"] == "5-1")["text"] == "autho- rized."


def test_syllables_drops_an_unknown_hyphen_but_keeps_a_known_compound():
    near = {"gap_before": 13.0}
    text = hyphen_text(
        ("Any ambigu-", 72),
        ("ities in a decision-", 72, near),
        ("making process, by decision-making bodies, in three-", 72, near),
        ("tiered review of every three or more tiered plan.", 72, near),
        mode="syllables",
    )
    assert text == (
        "Any ambiguities in a decision-making process, by decision-making bodies, in "
        "three-tiered review of every three or more tiered plan."
    )
