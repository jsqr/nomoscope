"""Dates stated in a section's history text.

A set of drafting conventions gives a regular expression for each date,
with a group named date. It also gives the date format, and which match
to take: first when the text names one act, last when it lists acts
oldest first, latest when it lists them in no order. Text after the note
phrase is ignored.

Notes that give only a year, as "(Ord. 77-01, 1977)", give enacted_year
through a phrase with a group named year. A note with an enacted date also
gives its year.

format names a parser function, because conventions abbreviate months
differently (Sept., not Sep).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, fields
from datetime import date

MONTHS = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
MONTH_DATE = r"(?P<date>[A-Z][a-z]{2,8}\.? \d{1,2}, \d{4})"
MONTH_DATE_RE = re.compile(r"([A-Za-z]+)\.? (\d{1,2}), (\d{4})")
NUMERIC_DATE = r"(?P<date>\d{1,2}-\d{1,2}-\d{2,4})"
NUMERIC_DATE_RE = re.compile(r"(\d{1,2})-(\d{1,2})-(\d{2,4})")
YEAR = r"(?P<year>\d{4})"
# A two-digit year below PIVOT is read in this century, others in the last:
# "1-5-99" is 1999, "1-1-26" is 2026.
PIVOT = 50


@dataclass(frozen=True)
class Dates:
    enacted: date | None = None
    effective: date | None = None
    operative: date | None = None
    inoperative: date | None = None
    repeal: date | None = None
    enacted_year: int | None = None


YEARS = ("enacted_year",)
ASPECTS = tuple(f.name for f in fields(Dates) if f.name not in YEARS)


def month_day_year(written: str) -> date | None:
    m = MONTH_DATE_RE.fullmatch(written.strip())
    if not m:
        return None
    name = m.group(1).lower()
    month = next((i for i, full in enumerate(MONTHS, 1) if full.startswith(name)), None)
    if month is None:
        return None
    try:
        return date(int(m.group(3)), month, int(m.group(2)))
    except ValueError:
        return None


def numeric_mdy(written: str) -> date | None:
    m = NUMERIC_DATE_RE.fullmatch(written.strip())
    if not m:
        return None
    year = int(m.group(3))
    try:
        if year < 100:
            year += 2000 if year < PIVOT else 1900
        return date(year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


FORMATS = {"month_day_year": month_day_year, "numeric_mdy": numeric_mdy}


def _pick[T: (date, int)](found: list[T], which: str) -> T:
    if which == "latest":
        return max(found)
    return found[-1 if which == "last" else 0]


def parse(text: str | None, phrases: Mapping[str, str] | None) -> Dates:
    """The dates the text states, by one convention's phrases."""
    if not text or not phrases:
        return Dates()
    read = FORMATS[phrases["format"]]
    note = phrases.get("note")
    body = re.split(note, text, maxsplit=1)[0] if note else text
    which = phrases["which"]
    dates: dict[str, date] = {}
    for aspect in ASPECTS:
        pattern = phrases.get(aspect)
        if not pattern:
            continue
        stated = [d for m in re.finditer(pattern, body) if (d := read(m.group("date")))]
        if stated:
            dates[aspect] = _pick(stated, which)
    pattern = phrases.get("enacted_year")
    years = [int(m.group("year")) for m in re.finditer(pattern, body)] if pattern else []
    enacted = dates.get("enacted")
    year: int | None
    if which == "latest":
        found = [*years, *([enacted.year] if enacted else [])]
        year = max(found) if found else None
    else:
        year = enacted.year if enacted else (_pick(years, which) if years else None)
    return Dates(**dates, enacted_year=year)
