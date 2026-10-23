from datetime import date

import polars as pl
from polars.testing import assert_frame_equal

from nomoscope.derive import ledger

KEY = ("law_code", "section_num")


def seen(rows, publication, on, completeness="full", payload=()):
    frame = pl.DataFrame(
        rows,
        schema={"law_code": pl.String, "section_num": pl.String, "text": pl.String},
        orient="row",
    )
    return ledger.sightings(
        frame, KEY, payload, publication=publication, on=on, completeness=completeness
    )


P1 = [("m", "1", "a"), ("m", "2", "b")]
P2 = [("m", "2", "b2")]
P3 = [("m", "1", "a")]


def test_a_fold_keeps_first_last_and_counts_in_any_order():
    frames = [
        seen(P1, "p1", date(2024, 1, 1), payload=("text",)),
        seen(P2, "p2", date(2024, 6, 1), "partial", payload=("text",)),
        seen(P3, "p3", date(2025, 1, 1), payload=("text",)),
    ]
    forward = ledger.fold(frames, KEY, ("text",))
    backward = ledger.fold(list(reversed(frames)), KEY, ("text",))
    assert forward is not None and backward is not None
    assert_frame_equal(forward, backward)
    rows = {r["section_num"]: r for r in forward.to_dicts()}
    assert rows["1"] | {} == {
        "law_code": "m",
        "section_num": "1",
        "text": "a",
        "first_seen": date(2024, 1, 1),
        "last_seen": date(2025, 1, 1),
        "last_full_seen": date(2025, 1, 1),
        "observations": 2,
        "first_publication": "p1",
        "last_publication": "p3",
    }
    # The payload comes from the latest sighting. A partial publication sets no last_full_seen.
    assert rows["2"]["text"] == "b2"
    assert (rows["2"]["last_seen"], rows["2"]["last_full_seen"]) == (
        date(2024, 6, 1),
        date(2024, 1, 1),
    )


def test_folding_onto_a_ledger_agrees_with_refolding():
    frames = [
        seen(P1, "p1", date(2024, 1, 1)),
        seen(P2, "p2", date(2024, 6, 1), "partial"),
    ]
    stored = ledger.fold(frames, KEY)
    assert stored is not None
    onto = ledger.fold([stored, seen(P3, "p3", date(2025, 1, 1))], KEY)
    every = ledger.fold([*frames, seen(P3, "p3", date(2025, 1, 1))], KEY)
    assert onto is not None and every is not None
    assert_frame_equal(onto, every)


def test_nothing_to_fold_is_none():
    assert ledger.fold([], KEY) is None


def test_absent_from_is_the_next_census_of_the_same_code():
    publications = pl.DataFrame(
        {
            "law_codes": [["m"], ["m"], ["z"], ["m", "z"]],
            "published_date": [
                date(2024, 1, 1),
                date(2024, 6, 1),
                date(2024, 9, 1),
                date(2025, 1, 1),
            ],
            "completeness": ["full", "partial", "full", "full"],
        }
    )
    census = ledger.censuses(publications)
    assert census.to_dicts() == [
        {"law_code": "m", "census_date": date(2024, 1, 1)},
        {"law_code": "z", "census_date": date(2024, 9, 1)},
        {"law_code": "m", "census_date": date(2025, 1, 1)},
        {"law_code": "z", "census_date": date(2025, 1, 1)},
    ]
    rows = pl.DataFrame(
        {
            "law_code": ["m", "m", "m", "z"],
            "last_seen": [
                date(2024, 1, 1),
                date(2024, 6, 1),
                date(2025, 1, 1),
                date(2024, 1, 1),
            ],
        }
    )
    got = ledger.with_absent_from(rows, census)["absent_from"].to_list()
    # A full publication of z does not mark m absent. A row in the latest full
    # publication has no absent_from.
    assert got == [date(2025, 1, 1), date(2025, 1, 1), None, date(2024, 9, 1)]
