import tomllib

import pytest
from pydantic import ValidationError

from nomoscope.structure import grammar as gr


def level(**kw) -> gr.Level:
    return gr.Level(**{"kind": "division", "style": "arabic", **kw})


SECTION = level(name="section", label="§ {chapter}-{n}.", decimal=True)
GRAMMAR = gr.Grammar(
    levels=[
        level(name="chapter", label="Chapter {n}", heading="next_line"),
        SECTION,
        level(name="subsection", kind="subdivision", label="{n}.", style="upper_alpha"),
    ],
)


def matched(m: gr.Matcher, text: str) -> gr.Match:
    found = gr.match(m, text)
    assert found is not None
    return found


def test_match():
    m = matched(gr.compile(SECTION), "§ 121-52.1 . Solar energy.")
    assert (m.number, m.enum.ordinal, m.enum.sub) == ("121-52.1", 52, 1)
    assert m.refs == {"chapter": "121"}
    assert m.rest == "Solar energy."
    assert gr.match(gr.compile(SECTION), "§ 1-1 Intent.") is None


def test_loose_relaxes_only_trailing_punctuation():
    m = matched(gr.compile(SECTION, loose=True), "§\xa010-1 Purpose.")
    assert (m.number, m.refs) == ("10-1", {"chapter": "10"})


def test_literal_words_ignore_case_and_need_a_boundary():
    chapter = gr.compile(GRAMMAR.levels[0])
    assert matched(chapter, "chapter 7: Claims").number == "7"
    assert gr.match(chapter, "Chapter 7x") is None


def test_numbers_anywhere():
    text = "Title. § 121-16 . Mixed use § 121-2 . Guide"
    assert gr.numbers(gr.compile(SECTION), text) == ["121-16", "121-2"]


def test_classify_by_order():
    ms = gr.matchers(GRAMMAR)
    found = gr.classify("A. Permits", ms)
    assert found is not None and found.level.name == "subsection"
    assert gr.classify("A person shall", ms) is None


def test_toml_round_trip():
    text = gr.dumps(GRAMMAR, "by hand")
    assert text.startswith("# by hand\n")
    assert text.isascii()
    assert gr.Grammar.model_validate(tomllib.loads(text)) == GRAMMAR


def test_sections_round_trip():
    g = GRAMMAR.model_copy(
        update={"sections": gr.Sections(history=[r"^\(Ord\. "], drop=["^Editor's note"])}
    )
    assert gr.loads(gr.dumps(g)) == g


def test_a_review_block_is_rejected():
    text = gr.dumps(GRAMMAR) + '\n[review]\nstatus = "reviewed"\n'
    with pytest.raises(ValueError, match="grammar accept"):
        gr.loads(text)


def test_invalid():
    with pytest.raises(ValidationError):
        level(name="x", label="Chapter")
    with pytest.raises(ValidationError):
        level(name="x", label="{n}", style="greek")
    with pytest.raises(ValidationError):
        gr.Grammar(levels=[level(name="section", label="{chapter}-{n}")])
    with pytest.raises(ValidationError):
        gr.Grammar(
            levels=[
                level(name="a", kind="subdivision", label="({n})"),
                level(name="b", label="Part {n}"),
            ],
        )
    with pytest.raises(ValidationError):
        gr.Sections(history=["(unclosed"])


def test_nested_objects_written_as_json_strings():
    # A model sent "skip" as the string '{"top": 70, "bottom": 40}'.
    g = gr.Grammar.model_validate(
        {
            "skip": '{"top": 70, "bottom": 752}',
            "levels": [
                {
                    "name": "section",
                    "kind": "division",
                    "label": "{n}.",
                    "style": "arabic",
                    "signals": '{"bold": true, "x": 72}',
                }
            ],
        }
    )
    assert (g.skip.top, g.skip.bottom) == (70, 752)
    assert (g.levels[0].signals.bold, g.levels[0].signals.x) == (True, 72)


def test_ancestor_reference_takes_all_it_can():
    # "2.12.070" read as chapter 2, number 12 when the reference matched as
    # little as it could.
    section = level(name="section", label="§ {chapter}.{n}.")
    m = matched(gr.compile(section), "§ 2.12.070. Conduct of trial.")
    assert (m.refs, m.number, m.enum.ordinal) == ({"chapter": "2.12"}, "2.12.070", 70)
    assert matched(gr.compile(SECTION), "§ 67-2. Purpose.").refs == {"chapter": "67"}


def test_bands_are_y_values_from_the_top():
    assert gr.Skip(top=70, bottom=730).bottom == 730
    with pytest.raises(ValidationError):
        gr.Skip(top=70, bottom=40)


def lead(form: gr.Form, text: str, start: int = 0, bold=()):
    spans = [{"start": s, "end": e} for s, e in bold]
    return gr.match_lead(form, gr.compile_form(form), text, start, spans)


def test_lead_forms():
    dash = gr.Form(form="{lead} —", caps=True)
    assert lead(dash, "BOARD OF APPEALS — The Zoning Board") == gr.Lead("BOARD OF APPEALS", 18)
    assert getattr(lead(dash, "BUILDING -- Any structure"), "text", None) == "BUILDING"
    assert lead(dash, "Board — The Board") is None
    # caps reads the first word, so terms may join synonyms with "or".
    assert getattr(lead(dash, "FLOOD or FLOODING — A flow"), "text", None) == "FLOOD or FLOODING"
    # A space in the form needs whitespace: a hyphen in a word is no dash.
    assert getattr(lead(dash, "FLOODPLAIN or FLOOD-PRONE AREA — Land"), "text", None) == (
        "FLOODPLAIN or FLOOD-PRONE AREA"
    )
    assert lead(dash, "APPOINTED pursuant to § 89-") is None
    quoted = gr.Form(form='"{lead}" means')
    assert getattr(lead(quoted, "“Acceptance” means to regard"), "text", None) == "Acceptance"
    assert lead(quoted, '"Equity" shall mean') is None


def test_bold_lead_is_a_whole_run():
    bold = gr.Form(form="{lead}.", bold=True)
    found = lead(bold, "U.S. Post Office. Text", bold=[(0, 17)])
    assert found is not None and found.text == "U.S. Post Office"
    found = lead(bold, "A. Access, Private. Text", 3, bold=[(3, 19)])
    assert found is not None and found.text == "Access, Private"
    assert lead(bold, "Access. The provision") is None
    # A bold run that is not the whole form, such as a bold first word.
    assert lead(bold, "Notice. Given in writing.", bold=[(0, 3)]) is None


def test_form_needs_one_lead():
    with pytest.raises(ValueError):
        gr.Form(form="{n}.")


def test_a_level_that_is_not_spaced_needs_its_pieces_to_touch():
    tight = gr.compile(level(name="sub", kind="subdivision", label="{n}.", style="upper_alpha",
                             spaced=False))  # fmt: skip
    assert matched(tight, "A. Permits.").number == "A"
    assert gr.match(tight, "I . . . do solemnly swear") is None
    assert matched(gr.compile(level(name="sub", kind="subdivision", label="{n}.",
                                    style="upper_alpha")), "I . . . do").number == "I"  # fmt: skip
    section = gr.compile(level(name="section", label="Section {n}.", spaced=False))
    assert matched(section, "Section  12. Title").number == "12"
    assert gr.match(section, "Section 12 . Title") is None
