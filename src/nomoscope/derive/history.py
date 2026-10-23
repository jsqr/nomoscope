"""Dates stated in a section's history text.

A set of drafting conventions gives a regular expression for each date,
with a group named date. It also gives the date format, and which match
to take: first when the text names one act, last when it lists acts
oldest first. Text after the note phrase is ignored.

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
# A two-digit year is read in this century.
CENTURY = 2000


@dataclass(frozen=True)
class Dates:
    enacted: date | None = None
    effective: date | None = None
    operative: date | None = None
    inoperative: date | None = None
    repeal: date | None = None


ASPECTS = tuple(f.name for f in fields(Dates))


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
        return date(year if year > 99 else CENTURY + year, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


FORMATS = {"month_day_year": month_day_year, "numeric_mdy": numeric_mdy}


def parse(text: str | None, phrases: Mapping[str, str] | None) -> Dates:
    """The dates the text states, by one convention's phrases."""
    if not text or not phrases:
        return Dates()
    read = FORMATS[phrases["format"]]
    note = phrases.get("note")
    body = re.split(note, text, maxsplit=1)[0] if note else text
    last = phrases["which"] == "last"
    found = {}
    for aspect in ASPECTS:
        pattern = phrases.get(aspect)
        if not pattern:
            continue
        matches = list(re.finditer(pattern, body))
        if not matches:
            continue
        when = read(matches[-1 if last else 0].group("date"))
        if when is not None:
            found[aspect] = when
    return Dates(**found)
