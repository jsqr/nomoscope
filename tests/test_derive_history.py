from datetime import date

from nomoscope.derive import history
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
