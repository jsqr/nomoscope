from datetime import date

import polars as pl

from nomoscope.derive import datasets, history
from nomoscope.derive.conventions import Conventions
from tests.conventions import CONVENTIONS


def parse(text, drafting):
    return history.parse(text, CONVENTIONS.phrases.get(drafting))


def test_california_phrases():
    d = parse(
        "Amended by Stats. 2024, Ch. 482, Sec. 20.   (SB 1452)   Effective January 1, 2025."
        "   Operative July 1, 2025, by Sec. 30.   Repealed as of January 1, 2029, by its own"
        " provisions.",
        "ca",
    )
    assert d == history.Dates(
        effective=date(2025, 1, 1), operative=date(2025, 7, 1), repeal=date(2029, 1, 1)
    )


def test_inoperative_forms_and_notes_are_ignored():
    assert parse("Conditionally inoperative on February 1, 2015.", "ca") == (
        history.Dates(inoperative=date(2015, 2, 1))
    )
    assert parse("Inoperative January 1, 2036, pursuant to Section 25299.", "ca") == (
        history.Dates(inoperative=date(2036, 1, 1))
    )
    note = (
        "Added by Stats. 1979, Ch. 1124.   Note: Repeal of previous version is operative"
        " April 1, 1993."
    )
    assert parse(note, "ca") == history.Dates()


def test_missing_text_or_conventions():
    assert parse(None, "ca") == history.Dates()
    assert parse("Effective January 1, 2025.", "us-xx") == history.Dates()
    assert parse("Effective Febtember 31, 2025.", "ca") == history.Dates()


def test_texas_takes_the_last_act_in_the_chain():
    """Texas lists acts oldest first, so the last effective date applies."""
    chain = (
        "Acts 1981, 67th Leg., p. 1015, ch. 388, Sec. 1, eff. Sept. 1, 1981."
        "  Amended by Acts 1985, 69th Leg., ch. 479, Sec. 66, eff. Sept. 1, 1985."
        "\nActs 2007, 80th Leg., R.S., Ch. 1094 (H.B. 3300), Sec. 1, eff. June 15, 2007."
    )
    assert parse(chain, "tx") == history.Dates(effective=date(2007, 6, 15))


def test_texas_month_abbreviations_are_its_own():
    """strptime does not know Sept. or Aug."""
    for written, expected in [
        ("eff. Sept. 1, 1981.", date(1981, 9, 1)),
        ("eff. September 1, 2023.", date(2023, 9, 1)),
        ("eff. Aug. 30, 2021.", date(2021, 8, 30)),
        ("eff. Oct. 20, 1987.", date(1987, 10, 20)),
    ]:
        assert parse(written, "tx").effective == expected


def test_texas_expiry_is_inoperative_not_repeal():
    assert parse(
        "Acts 2019, 86th Leg., R.S., Ch. 1, Sec. 2, eff. June 1, 2019."
        "  This section expires September 1, 2027.",
        "tx",
    ) == history.Dates(effective=date(2019, 6, 1), inoperative=date(2027, 9, 1))


def test_illinois_source_note_chain():
    note = (
        "(Source: P.A. 103-210, eff. 7-1-24; 103-933, eff. 1-1-25; 104-234, eff. 8-15-25;"
        " 104-443, eff. 1-1-26.)"
    )
    assert parse(note, "il") == history.Dates(effective=date(2026, 1, 1))
    assert parse("(Source: P.A. 99-860, eff. 1-1-17.)", "il") == (
        history.Dates(effective=date(2017, 1, 1))
    )


def test_a_date_that_does_not_exist_is_not_read():
    assert parse("eff. 2-30-25.", "il") == history.Dates()
    assert parse("eff. Febtember 3, 2025.", "tx") == history.Dates()


def test_a_two_digit_year_is_read_in_its_century():
    assert history.numeric_mdy("1-5-99") == date(1999, 1, 5)
    assert history.numeric_mdy("1-1-26") == date(2026, 1, 1)


def test_the_latest_date_and_a_year_alone():
    latest = {"format": "numeric_mdy", "which": "latest", "enacted": r"{NUMERIC_DATE}"}
    phrases = {k: v.replace("{NUMERIC_DATE}", history.NUMERIC_DATE) for k, v in latest.items()}
    note = "[Amended 6-16-2005; 4-18-1991; 12-18-2025 by L.L. No. 2-2025]"
    assert history.parse(note, phrases) == history.Dates(
        enacted=date(2025, 12, 18), enacted_year=2025
    )
    years = {"format": "numeric_mdy", "which": "latest", "enacted_year": rf", {history.YEAR}\)"}
    assert history.parse("(Ord. 77-01 (part), 1977; Ord. 81-09, 1981)", years) == (
        history.Dates(enacted_year=1981)
    )
    both = {**years, "enacted": rf", {history.NUMERIC_DATE}(?=[;)])"}
    assert history.parse("(Ord. 98-13, 5-28-98; Ord. 2005-3, 2005)", both) == (
        history.Dates(enacted=date(1998, 5, 28), enacted_year=2005)
    )


def test_a_section_without_history_takes_its_divisions_dates():
    phrases = {"format": "numeric_mdy", "which": "latest", "enacted": history.NUMERIC_DATE}
    found = Conventions(phrases={"bracketed": phrases})
    frame = pl.DataFrame(
        {
            "history": ["[Amended 6-16-2005]", None],
            "drafting": ["bracketed", "bracketed"],
            "division_history": [None, "[Adopted 4-18-1991]"],
        }
    )
    dated = frame.with_columns(datasets.stated_dates(frame, found))
    assert dated["enacted_date"].to_list() == [date(2005, 6, 16), date(1991, 4, 18)]
    assert dated["enacted_year"].to_list() == [2005, 1991]
