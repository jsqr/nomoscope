"""Definition, penalty, and citation notes for retrieved passages, as of the
question's date.

A definition applies when its version is in force, its term appears in the
passage, and its scope covers the passage's section: that section or a
subdivision of it, a heading above it, or its code. A definition with no
scope applies only in its own section. Of two definitions of one term, the
narrower scope wins. A definition already inside the passage is left out.

Penalties apply by the same scope test, a general penalty covering its code.
All that apply are noted, narrowest first.

A cited provision is used when the citation lies in the passage's span and
resolved to a section, a subdivision, or a heading. A heading is named
without its text. Ranges, and citations of the passage's own section or of a
heading above it, are left out.

With lookups on, a citation of an unloaded section or subdivision gets the
text a web lookup finds (`lookup/run.py`), for at most `lookup_limit` new
lookups per call; cached ones are free and do not count.

Each note's text is given once, under the first passage that needs it;
later passages point back to it.
"""

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date

import duckdb

from nomoscope.answer.models import AnswerError
from nomoscope.answer.prompt import DEFINITION, PENALTY, REFERENCE, Note, Passage, Placed
from nomoscope.lookup.run import Looked, Request
from nomoscope.partition import dataset_path
from nomoscope.search.query import Hit
from nomoscope.settings import Settings as Config

DEFINITIONS = "definitions"
PENALTIES = "penalties"
REFERENCES = "cross_references"
NOTE_CHARS = 1200
NOTES = 8
LOOKUP_LIMIT = 4

# The request, and whether it may search; without, only the cache is read.
Lookup = Callable[[Request, bool], Looked]
# Each heading's latest wording, named with its level word when its number
# lacks one: "Article IV Removal of Snow and Ice".
HEADINGS = """
SELECT id, trim(concat_ws(' ',
    CASE WHEN level IS NOT NULL AND NOT starts_with(lower(coalesce(number, '')), lower(level))
         THEN upper(level[1]) || level[2:] END,
    number, heading)) AS name
FROM law_nodes WHERE jurisdiction = $j
QUALIFY row_number() OVER (PARTITION BY id ORDER BY last_seen DESC) = 1
"""
# Narrower scopes rank lower. A heading's rank adds its depth above the section.
SUBDIVISION_RANK = 0
SECTION_RANK = 1
NODE_RANK = 2
CODE_RANK = 1000


@dataclass(frozen=True)
class Settings:
    definitions: bool = False
    references: bool = False
    note_chars: int = NOTE_CHARS
    notes: int = NOTES
    penalties: bool = False
    lookups: bool = False
    lookup_limit: int = LOOKUP_LIMIT

    @property
    def on(self) -> bool:
        return self.definitions or self.references or self.penalties or self.lookups


def check(cfg: Config, s: Settings, jurisdictions: Sequence[str]) -> None:
    for wanted, dataset, task in (
        (s.definitions, DEFINITIONS, "definitions"),
        (s.penalties, PENALTIES, "penalties"),
        (s.references or s.lookups, REFERENCES, "references"),
    ):
        if not wanted:
            continue
        missing = [j for j in jurisdictions if not dataset_path(cfg, dataset, j).is_file()]
        if missing:
            raise AnswerError(
                f"[answers] {task} = true needs the {dataset} dataset for {', '.join(missing)};"
                f" build it with `extract {task}`"
            )


@dataclass(frozen=True)
class Place:
    section_id: str
    code_id: str
    ancestors: Mapping[str, int]
    start: int
    end: int


def places(
    conn: duckdb.DuckDBPyConnection, jurisdiction: str, hits: Sequence[Hit]
) -> dict[str, Place]:
    """Keyed by segment id."""
    segments = [h.segment_id for h in hits]
    rows = conn.execute(
        """
        SELECT g.id, v.section_id, v.law_code, g.start_char, g.end_char
        FROM segments g JOIN law_versions v ON v.jurisdiction = g.jurisdiction AND v.id = g.block_id
        WHERE g.jurisdiction = ? AND list_contains(?, g.id)
        """,
        [jurisdiction, segments],
    ).fetchall()
    sections = sorted({r[1] for r in rows})
    above: dict[str, dict[str, int]] = {}
    for descendant, ancestor, depth in conn.execute(
        "SELECT descendant_id, ancestor_id, min(depth) FROM law_ancestors"
        " WHERE jurisdiction = ? AND list_contains(?, descendant_id) GROUP BY ALL",
        [jurisdiction, sections],
    ).fetchall():
        above.setdefault(descendant, {})[ancestor] = depth
    return {
        segment: Place(section, f"{jurisdiction}/law/{code}", above.get(section, {}), start, end)
        for segment, section, code, start, end in rows
    }


def shortened(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[:limit]
    if not (cut[-1].isspace() or text[limit].isspace()):
        cut = cut.rsplit(None, 1)[0]
    return f"{cut.rstrip()} ..."


@dataclass(frozen=True)
class Definition:
    id: str
    term: str
    term_key: str
    text: str
    section_id: str
    citation: str
    scope_kind: str | None
    scope_id: str | None
    scope_name: str | None


def definitions(conn: duckdb.DuckDBPyConnection, jurisdiction: str, on: date) -> list[Definition]:
    rows = conn.execute(
        f"""
        SELECT d.id, d.term, d.term_key, d.text, d.section_id,
               l.law_code || ' ' || l.section_num, d.scope_kind, d.scope_id,
               coalesce(n.name, d.scope_text)
        FROM definitions d
        JOIN law_at($j, $on) l ON l.id = d.block_id
        LEFT JOIN ({HEADINGS}) n ON n.id = d.scope_id
        WHERE d.jurisdiction = $j AND d.source = 'text'
        ORDER BY d.id
        """,
        {"j": jurisdiction, "on": on},
    ).fetchall()
    return [Definition(*r) for r in rows]


def rank(scope_kind: str | None, scope_id: str | None, home: str, place: Place) -> int | None:
    """Lower is narrower; None if the scope does not cover the passage. An item
    with no scope covers only its own section, `home`."""
    if scope_id is None:
        return SECTION_RANK if home == place.section_id else None
    if scope_id == place.section_id:
        return SECTION_RANK
    if scope_kind == "subdivision" and scope_id.startswith(place.section_id + "/"):
        return SUBDIVISION_RANK
    if scope_id in place.ancestors:
        return NODE_RANK + place.ancestors[scope_id]
    if scope_id == place.code_id:
        return CODE_RANK
    return None


def uses(term_key: str, text: str) -> int | None:
    """Offset of the first use; a plain plural counts."""
    m = re.search(rf"(?<!\w){re.escape(term_key)}(?:e?s)?(?!\w)", text, re.IGNORECASE)
    return m.start() if m else None


def applies_to(d: Definition) -> str:
    if d.scope_id is None:
        return "its own section"
    if d.scope_kind == "code":
        return "the whole code"
    return d.scope_name or d.scope_id


def definition_notes(found: Sequence[Definition], hit: Hit, place: Place, limit: int) -> list[Note]:
    best: dict[str, tuple[int, int, Definition]] = {}
    for d in found:
        r = rank(d.scope_kind, d.scope_id, d.section_id, place)
        at = uses(d.term_key, hit.snippet) if r is not None else None
        if r is None or at is None:
            continue
        if d.term_key not in best or r < best[d.term_key][0]:
            best[d.term_key] = (r, at, d)
    # A passage that prints the governing definition itself needs no note.
    snippet = " ".join(hit.snippet.split())
    chosen = sorted(
        (b for b in best.values() if " ".join(b[2].text.split()) not in snippet),
        key=lambda b: b[1],
    )
    return [
        Note(
            DEFINITION,
            d.id,
            d.term,
            f"{d.citation}, applies to {applies_to(d)}",
            shortened(d.text, limit),
        )
        for _, _, d in chosen
    ]


@dataclass(frozen=True)
class Penalty:
    id: str
    text: str
    section_id: str
    citation: str
    scope_kind: str | None
    scope_id: str | None
    scope_basis: str | None
    scope_name: str | None
    classification: str | None
    fine_min: float | None
    fine_max: float | None
    fine_per: str | None
    imprisonment_max_days: int | None
    offense: str | None
    each_day_separate: bool | None
    other: Sequence[str] | None


def penalties(conn: duckdb.DuckDBPyConnection, jurisdiction: str, on: date) -> list[Penalty]:
    rows = conn.execute(
        f"""
        SELECT p.id, p.text, p.section_id, l.law_code || ' ' || l.section_num,
               p.scope_kind, p.scope_id, p.scope_basis, coalesce(n.name, p.scope_text),
               p.classification, p.fine_min, p.fine_max, p.fine_per,
               p.imprisonment_max_days, p.offense, p.each_day_separate, p.other
        FROM penalties p
        JOIN law_at($j, $on) l ON l.id = p.block_id
        LEFT JOIN ({HEADINGS}) n ON n.id = p.scope_id
        WHERE p.jurisdiction = $j AND p.source = 'text'
        ORDER BY p.id
        """,
        {"j": jurisdiction, "on": on},
    ).fetchall()
    return [Penalty(*r) for r in rows]


def dollars(amount: float) -> str:
    return f"${amount:,.0f}" if amount == int(amount) else f"${amount:,.2f}"


def penalty_name(p: Penalty) -> str:
    """ "Violation; fine up to $250 per day; each day a separate offense"."""
    parts = [p.classification[:1].upper() + p.classification[1:]] if p.classification else []
    if p.offense in ("first", "second", "subsequent"):
        parts.append(f"{p.offense} offense")
    if p.fine_max is not None or p.fine_min is not None:
        if p.fine_min is not None and p.fine_max is not None and p.fine_min != p.fine_max:
            fine = f"fine {dollars(p.fine_min)} to {dollars(p.fine_max)}"
        else:
            fine = f"fine up to {dollars(p.fine_max or p.fine_min or 0)}"
        parts.append(fine + (" per day" if p.fine_per == "day" else ""))
    if p.imprisonment_max_days:
        parts.append(f"imprisonment up to {p.imprisonment_max_days} days")
    if p.each_day_separate:
        parts.append("each day a separate offense")
    parts += [o.replace("_", " ") for o in p.other or () if o != "other"]
    name = "; ".join(parts) or "Other consequence"
    return name[:1].upper() + name[1:]


def penalty_notes(found: Sequence[Penalty], hit: Hit, place: Place, limit: int) -> list[Note]:
    snippet = " ".join(hit.snippet.split())
    ranked = []
    for p in found:
        r = rank(p.scope_kind, p.scope_id, p.section_id, place)
        if r is not None and " ".join(p.text.split()) not in snippet:
            ranked.append((r, p))
    ranked.sort(key=lambda rp: (rp[0], rp[1].id))
    notes = []
    for _, p in ranked:
        if p.scope_basis == "default":
            covers = "the general penalty for the code"
        elif p.scope_id is None:
            covers = "applies to its own section"
        elif p.scope_kind == "code":
            covers = "applies to the whole code"
        else:
            covers = f"applies to {p.scope_name or p.scope_id}"
        notes.append(
            Note(
                PENALTY, p.id, penalty_name(p), f"{p.citation}, {covers}", shortened(p.text, limit)
            )
        )
    return notes


def reference_notes(
    conn: duckdb.DuckDBPyConnection,
    jurisdiction: str,
    on: date,
    hit: Hit,
    place: Place,
    limit: int,
) -> list[Note]:
    rows = conn.execute(
        f"""
        SELECT r.target_id, r.target_kind, r.text,
               coalesce(s.law_code || ' ' || s.section_num, b.law_code || ' ' || b.section_num),
               coalesce(s.text, substr(b.text, u.start_char + 1, u.end_char - u.start_char)),
               u.path, coalesce(s.section_id, b.section_id),
               n.name
        FROM cross_references r
        LEFT JOIN law_at($j, $on) s ON r.target_kind = 'section' AND s.section_id = r.target_id
        LEFT JOIN law_subdivisions u
               ON r.target_kind = 'subdivision' AND u.subdivision_id = r.target_id
        LEFT JOIN law_at($j, $on) b ON b.id = u.law_version_id
        LEFT JOIN ({HEADINGS}) n ON r.target_kind = 'node' AND n.id = r.target_id
        WHERE r.jurisdiction = $j AND r.block_id = $block AND r.source = 'text'
          AND r.target_status = 'resolved' AND r.target_end_id IS NULL
          AND r.start_char >= $start AND r.end_char <= $end
        ORDER BY r.start_char
        """,
        {
            "j": jurisdiction,
            "on": on,
            "block": hit.law_version_id,
            "start": place.start,
            "end": place.end,
        },
    ).fetchall()
    found: dict[str, Note] = {}
    for target, kind, cited, citation, text, path, section, heading in rows:
        # A citation of the passage's own section or of a heading above it
        # ("this article") adds nothing.
        own = section == place.section_id or target in place.ancestors
        if target in found or own:
            continue
        if kind == "node":
            found[target] = Note(REFERENCE, target, heading or target, f'cited as "{cited}"', None)
        elif text is not None:
            sub = "".join(f"({p})" for p in path.split("/")) if path else ""
            found[target] = Note(
                REFERENCE, target, f"{citation}{sub}", f'cited as "{cited}"',
                shortened(text, limit),
            )  # fmt: skip
    return list(found.values())


@dataclass(frozen=True)
class Unloaded:
    target_id: str
    cited: str
    bluebook: str | None


def unloaded(
    conn: duckdb.DuckDBPyConnection, jurisdiction: str, hit: Hit, place: Place
) -> list[Unloaded]:
    """Citations in the passage of sections or subdivisions that are not loaded."""
    rows = conn.execute(
        """
        SELECT DISTINCT ON (target_id) target_id, text, bluebook
        FROM cross_references
        WHERE jurisdiction = $j AND block_id = $block AND source = 'text'
          AND target_status = 'unloaded' AND target_kind IN ('section', 'subdivision')
          AND target_end_id IS NULL AND start_char >= $start AND end_char <= $end
        ORDER BY target_id, start_char
        """,
        {
            "j": jurisdiction,
            "block": hit.law_version_id,
            "start": place.start,
            "end": place.end,
        },
    ).fetchall()
    return [Unloaded(*r) for r in rows]


def looked_up_note(u: Unloaded, looked: Looked, limit: int) -> Note | None:
    f = looked.found
    if f is None:
        return None
    site = f.url.split("/")[2] if "//" in f.url else f.url
    about = f'cited as "{u.cited}"; current text from {site}, retrieved {f.looked_at.isoformat()}'
    return Note(REFERENCE, u.target_id, u.bluebook or u.cited, about, shortened(f.text, limit))


def assign(
    passages: Sequence[Passage], notes: Mapping[str, Iterable[Note]], cap: int
) -> list[Passage]:
    """`notes` is keyed by segment id. A note's text goes under the first
    passage that needs it; later ones get a pointer."""
    given: dict[str, str] = {}
    out = []
    for p in passages:
        placed = []
        for n in list(notes.get(p.hit.segment_id, ()))[:cap]:
            placed.append(Placed(n, given.get(n.id)))
            given.setdefault(n.id, p.label)
        out.append(replace(p, notes=tuple(placed)))
    return out


def augment(
    conn: duckdb.DuckDBPyConnection,
    jurisdiction: str,
    on: date,
    s: Settings,
    passages: Sequence[Passage],
    lookup: Lookup | None = None,
    place_name: Callable[[str], str | None] = lambda j: None,
) -> tuple[list[Passage], float]:
    """The passages with their notes, and what lookups spent. `place_name`
    names a cited jurisdiction for a query without a Bluebook form."""
    if not s.on or not passages:
        return list(passages), 0.0
    where = places(conn, jurisdiction, [p.hit for p in passages])
    found = definitions(conn, jurisdiction, on) if s.definitions else []
    fines = penalties(conn, jurisdiction, on) if s.penalties else []
    notes: dict[str, list[Note]] = {}
    looked: dict[str, Looked] = {}
    new = 0
    dollars = 0.0
    for p in passages:
        place = where.get(p.hit.segment_id)
        if place is None:
            continue
        mine = definition_notes(found, p.hit, place, s.note_chars) if s.definitions else []
        mine += penalty_notes(fines, p.hit, place, s.note_chars) if s.penalties else []
        if s.references:
            mine += reference_notes(conn, jurisdiction, on, p.hit, place, s.note_chars)
        if s.lookups and lookup is not None:
            for u in unloaded(conn, jurisdiction, p.hit, place):
                if u.target_id not in looked:
                    request = Request(
                        u.target_id, u.cited, u.bluebook, place_name(u.target_id.split("/")[0])
                    )
                    looked[u.target_id] = lookup(request, new < s.lookup_limit)
                    new += looked[u.target_id].searched
                    dollars += looked[u.target_id].dollars
                note = looked_up_note(u, looked[u.target_id], s.note_chars)
                if note is not None:
                    mine.append(note)
        notes[p.hit.segment_id] = mine
    return assign(passages, notes, s.notes), dollars
