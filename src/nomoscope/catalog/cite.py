"""Look up a section, or one of its subdivisions, by citation.

A citation is a section number and a path below it, as people write
them: `67-14(A)(1)`, `67-14A(1)`, `67-14 A(1)`, with or without a leading
section sign. A run-in term is named by its slug: `67-3/building`. The
section is the longest of the code's section numbers that starts the
citation, so `67-14A(1)` is section 67-14A if the code has one.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from nomoscope.catalog import build
from nomoscope.settings import Settings

PATH_TOKEN = re.compile(r"[^\s()/]+")


def parse(ref: str, sections: Iterable[str]) -> tuple[str, str] | None:
    """Split a citation into a section number and a path ("A/1"), or return None."""
    ref = ref.replace("\xa0", " ").strip().lstrip("§").strip()
    found = sorted((s for s in sections if ref.startswith(s)), key=len, reverse=True)
    if not found:
        return None
    section = found[0]
    return section, "/".join(PATH_TOKEN.findall(ref[len(section) :]))


@dataclass(frozen=True)
class Cited:
    jurisdiction: str
    law_code: str
    section_num: str
    path: str
    level: str | None
    heading: str | None
    text: str
    law_version_id: str
    start_date: date
    start_basis: str
    end_date: date | None
    end_basis: str


def lookup(
    cfg: Settings, jurisdiction: str, code: str, ref: str, on: date | None = None
) -> Cited | None:
    """The section or subdivision `ref` names, in the version in force on `on` (default today)."""
    conn = build.connect(cfg)
    try:
        sections = conn.execute(
            "SELECT DISTINCT section_num FROM law_versions WHERE jurisdiction = ? AND law_code = ?",
            [jurisdiction, code],
        ).fetchall()
        parsed = parse(ref, (s for (s,) in sections))
        if parsed is None:
            return None
        found = conn.execute(
            "SELECT * FROM subdivision_at(?, ?, ?, ?, ?)",
            [jurisdiction, code, *parsed, on or date.today()],
        )
        row = found.fetchone()
        names = [d[0] for d in found.description or []]
    finally:
        conn.close()
    return Cited(**dict(zip(names, row, strict=True))) if row else None
