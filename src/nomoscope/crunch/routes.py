"""Route registered records to crunchers.

The routes are values in the project's registry. A route names a source,
a jurisdiction, a code, and a cruncher. "*" matches any value. A
record that carries every code has an empty code, which only "*" matches.
valid_from, if set, is the first record date the route applies to. A
record's date is its last-modified time, else its fetch time.

The most specific matching route wins: the one that names the most
fields, then the one with the latest valid_from. A tie between different
crunchers is an error. A record that no route matches is not crunched.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from nomoscope.crunch import CrunchError
from nomoscope.graph.records import RecordRow

ANY = "*"


class RouteError(CrunchError):
    pass


@dataclass(frozen=True)
class Route:
    source: str
    jurisdiction: str
    law_code: str
    cruncher: str
    valid_from: date | None = None

    def matches(self, row: RecordRow) -> bool:
        return (
            self.source in (ANY, row.source)
            and self.jurisdiction in (ANY, row.jurisdiction)
            and self.law_code in (ANY, row.code)
            and (self.valid_from is None or self.valid_from <= row.published[0])
        )

    @property
    def rank(self) -> tuple[int, date]:
        named = sum(v != ANY for v in (self.source, self.jurisdiction, self.law_code))
        return named, self.valid_from or date.min


Routes = tuple[Route, ...]


def route(routes: Iterable[Route], row: RecordRow) -> str | None:
    """The cruncher for row, or None if no route matches."""
    found = sorted((r for r in routes if r.matches(row)), key=lambda r: r.rank)
    if not found:
        return None
    best = found[-1]
    tied = {r.cruncher for r in found if r.rank == best.rank}
    if len(tied) > 1:
        raise RouteError(f"{row.source}/{row.record}: routes tie between crunchers {sorted(tied)}")
    return best.cruncher


def routed(routes: Routes, rows: Sequence[RecordRow]) -> dict[str, list[RecordRow]]:
    """Group the crunchable rows by cruncher, keeping their order."""
    found: dict[str, list[RecordRow]] = {}
    for row in rows:
        if not row.crunchable:
            continue
        name = route(routes, row)
        if name is not None:
            found.setdefault(name, []).append(row)
    return found
