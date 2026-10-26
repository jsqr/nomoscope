import hashlib

from nomoscope.structure import documents
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
