from itertools import pairwise

import pytest

from nomoscope.derive import segment, subdivide
from nomoscope.derive.segment import Limit, Segment

MAX_CHARS = 2000
OVERLAP_CHARS = 200


def packed(text: str, max_chars: int = MAX_CHARS, overlap_chars: int = OVERLAP_CHARS):
    return segment.pack(segment.units(text, max_chars), max_chars, overlap_chars)


def covered(text: str, spans: list[segment.Span]) -> set[int]:
    return {i for s in spans for i in range(s.start, s.end)}


def test_every_non_space_character_is_covered():
    text = "\n".join(f"({chr(97 + i)}) " + "word " * 120 for i in range(6))
    spans = packed(text)
    seen = covered(text, spans)
    assert len(spans) > 1
    assert all(i in seen for i, c in enumerate(text) if not c.isspace())


def test_no_segment_exceeds_the_limit():
    text = "\n".join("word " * 200 for _ in range(20))
    assert all(s.chars <= MAX_CHARS for s in packed(text))


def test_segments_overlap_and_advance():
    text = "\n".join(f"({chr(97 + i)}) " + "word " * 30 for i in range(16))
    spans = packed(text, max_chars=1000, overlap_chars=200)
    assert len(spans) > 2
    assert all(b.start < a.end for a, b in pairwise(spans))
    assert all(b.start > a.start for a, b in pairwise(spans))
    assert spans[-1].end == len(text)


def test_overlap_repeats_whole_units_or_none():
    text = "\n".join(f"({chr(97 + i)}) " + "word " * 150 for i in range(4))
    spans = packed(text, max_chars=1000, overlap_chars=200)
    assert [s.chars for s in spans] == [754] * 4
    assert all(b.start > a.end for a, b in pairwise(spans))


def test_sentences_split_a_long_paragraph():
    paragraph = " ".join(f"The Legislature finds that thing number {i} is true." for i in range(60))
    spans = packed(paragraph, max_chars=400, overlap_chars=50)
    assert len(spans) > 1
    assert all(paragraph[s.start].isupper() or paragraph[s.start] == "(" for s in spans)


def test_abbreviations_and_section_numbers_do_not_break():
    body = "See Sec. 12 and Cal. Code 65920.5. The rule applies."
    assert segment.sentences(body, segment.Span(0, len(body))) == [
        segment.Span(0, len(body)),
    ]


def test_a_real_sentence_breaks():
    body = "The permit is granted. The applicant shall pay."
    assert segment.sentences(body, segment.Span(0, len(body))) == [
        segment.Span(0, 22),
        segment.Span(23, len(body)),
    ]


def test_an_unbroken_run_is_split_on_whitespace():
    text = "word " * 3000
    spans = packed(text)
    assert len(spans) > 1
    assert all(s.chars <= MAX_CHARS for s in spans)
    assert spans[-1].end == len(text)


def test_content_and_hash_agree():
    body = "the text of the span"
    assert segment.content("GOV 1.", body) == "GOV 1.\n\nthe text of the span"
    assert segment.content(None, body) == body
    assert segment.content_sha256("GOV 1.", body) != segment.content_sha256(None, body)


W20 = Limit(20, "words")


def words(n: int, tag: str = "word") -> str:
    return " ".join(f"{tag}{i}" for i in range(n))


def test_a_block_within_the_limit_is_one_whole_segment():
    text = "(a) A short section.\n(b) With two subdivisions."
    assert segment.cut(text, W20) == [Segment(0, len(text), (), 0, 0, (), "whole")]


def test_blank_text_yields_nothing():
    assert segment.cut("", W20) == []
    assert segment.cut("  \n\n ", W20) == []


def test_subdivisions_that_fit_are_the_segments():
    lead = "The agency shall do all of the following:"
    text = "\n".join([lead, f"(a) {words(15)}", f"(b) {words(15)}", f"(c) {words(15)}"])
    found = segment.cut(text, W20)
    assert [p.path for p in found] == [("a",), ("b",), ("c",)]
    assert [(p.position, p.siblings) for p in found] == [(1, 3), (2, 3), (3, 3)]
    assert all(p.kind == "subdivision" and p.chapeau == (lead,) for p in found)
    assert text[found[1].start : found[1].end].startswith("(b) ")
    assert found[-1].end == len(text)


def test_a_subdivision_over_the_limit_descends_with_its_own_chapeau():
    text = "\n".join(
        [
            f"(a) {words(10)}",
            "(b) The employer shall do as follows:",
            f"(1) {words(15)}",
            f"(2) {words(15)}",
        ]
    )
    found = segment.cut(text, W20)
    assert [p.path for p in found] == [("a",), ("b", "1"), ("b", "2")]
    assert found[0].chapeau == ()
    assert found[1].chapeau == ("(b) The employer shall do as follows:",)
    assert [(p.position, p.siblings) for p in found[1:]] == [(1, 2), (2, 2)]


def test_a_leaf_over_the_limit_is_packed_in_pieces():
    text = "\n".join([f"(a) {words(5)}", f"(b) {words(80)}"])
    found = segment.cut(text, W20)
    packed = [p for p in found if p.kind == "packed"]
    assert found[0].kind == "subdivision" and found[0].path == ("a",)
    assert len(packed) > 1
    assert all(p.path == ("b",) and (p.position, p.siblings) == (2, 2) for p in packed)
    assert [p.piece for p in packed] == list(range(1, len(packed) + 1))
    assert packed[0].start == found[0].end + 1 and packed[-1].end == len(text)
    assert all(a.start < b.start <= a.end + 1 for a, b in pairwise(packed))
    assert all(W20.measure(text[p.start : p.end]) <= W20.size * 1.2 for p in packed)


def test_a_block_with_no_subdivisions_is_packed_whole():
    text = words(100)
    found = segment.cut(text, W20)
    assert len(found) > 1
    assert all(
        p.kind == "packed" and p.path == () and (p.position, p.siblings) == (0, 0) for p in found
    )
    assert found[0].start == 0 and found[-1].end == len(text)


def test_tokens_count_by_characters():
    limit = Limit(10, "tokens")
    text = "x" * int(10 * segment.CHARS_PER_TOKEN)
    assert limit.fits(text)
    assert not limit.fits(text + "x")


def test_a_bad_limit_is_refused():
    with pytest.raises(segment.SegmentError):
        Limit(10, "pages")
    with pytest.raises(segment.SegmentError):
        Limit(0)


def test_the_same_text_gives_the_same_segments():
    text = "\n".join([f"({c}) {words(30)}" for c in "abcdef"])
    assert segment.cut(text, W20) == segment.cut(text, W20)


def test_indents_reach_the_parser():
    """(1) at the deeper indent is a child of (a), and (b) at the first indent
    closes it. So (a), over the limit, splits into (a)(1), with (a)'s text as
    the chapeau.
    """
    lines = [f"(a) {words(15)}", f"(1) {words(15)}", f"(b) {words(15)}"]
    text = "\n".join(lines)
    found = segment.cut(text, W20, indents=[7, 13, 7])
    assert [p.path for p in found] == [("a", "1"), ("b",)]
    assert found[0].chapeau == (lines[0],) and found[1].chapeau == ()


def test_context_lines_up_headings_chapeau_and_place():
    p = Segment(0, 1, ("b", "1"), 2, 3, ("Lead in:", "(b) Then:"), "subdivision")
    assert segment.context("GOV > 1.", p) == "GOV > 1.\nLead in:\n(b) Then:"
    assert segment.context("GOV > 1.", p, positions=True).endswith("\n(b)(1) 2 of 3")
    whole = Segment(0, 1, (), 0, 0, (), "whole")
    assert segment.context(None, whole, positions=True) is None
    assert segment.context("GOV > 1.", whole, positions=True) == "GOV > 1."


def test_settings_tag_the_derive_version():
    assert segment.Settings(Limit(500, "tokens"), True).tag == "500tokens-pos"
    assert segment.DEFAULT.tag == "300words"


def test_a_list_s_closing_lines_are_context_for_every_item():
    text = "\n".join(
        [
            "(a) A person may appeal by doing both of the following:",
            f"(1) {words(15)}",
            f"(2) {words(15)}",
            "If the person fails to do either, the order is final.",
            f"(b) {words(5)}",
        ]
    )
    found = segment.cut(text, W20)
    assert [p.path for p in found] == [("a", "1"), ("a", "2"), ("b",)]
    assert found[0].closing == ("If the person fails to do either, the order is final.",)
    assert found[1].closing == found[0].closing and found[2].closing == ()
    assert text[found[1].start : found[1].end] == f"(2) {words(15)}"
    assert segment.context("GOV", found[0]).endswith(
        "the following:\nIf the person fails to do either, the order is final."
    )


def test_a_long_chapeau_keeps_its_enumerators_and_last_sentence():
    findings = " ".join(f"The Legislature finds that fact {i} is true." for i in range(40))
    lead = f"(b) {findings} Therefore the agency shall do all of the following:"
    text = "\n".join([lead, f"(1) {words(15)}", f"(2) {words(15)}"])
    found = segment.cut(text, W20)
    assert found[0].chapeau == ("(b) Therefore the agency shall do all of the following:",)
    short = f"(b) Short lead:\n(1) {words(15)}\n(2) {words(15)}"
    assert segment.cut(short, W20)[0].chapeau == ("(b) Short lead:",)


def test_cut_follows_carved_subdivisions():
    text = "A. " + "alpha " * 8 + "\nB. " + "beta " * 8
    b = text.index("B.")
    rows = [
        {"path": "A", "level": "subsection", "style": "upper_alpha", "number": "A",
         "start_char": 0, "end_char": b - 1},
        {"path": "B", "level": "subsection", "style": "upper_alpha", "number": "B",
         "start_char": b, "end_char": len(text)},
    ]  # fmt: skip
    pieces = segment.cut(text, segment.Limit(10), None, subdivide.from_carved(rows))
    assert [p.path for p in pieces] == [("A",), ("B",)]
    assert text[pieces[1].start : pieces[1].end].startswith("B. beta")
