"""Anchor a document's bookmarks to the lines where their headings start.

`entries` drops repeated bookmarks and splits each title into a level
word, a number, and a heading. `anchor` looks for each heading near its
bookmark's target, and at the top of the next page when the target is
at the foot of a page.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

from nomoscope.structure import Row

# A level word, perhaps abbreviated with a period (Ch.), and a space, or
# a section sign, then a number: one that
# starts with a digit; a short capitalized code joined to a number
# (A216-1, SR.010, DL-1); a roman numeral; or a short code alone (CHA).
NUMBER = (
    r"\d[\w.\-]*\w|\d"
    r"|[A-Z]{1,4}[.\-]?\d[\w.\-]*\w|[A-Z]{1,4}[.\-]?\d"
    r"|[IVXLC]+|[A-Z]{1,4}"
)
TITLE = re.compile(
    r"^\s*(?:(?P<sign>\xa7+)\s*|(?P<word>[A-Za-z]+)\.?\s+)"
    rf"(?P<number>{NUMBER})(?=$|[\s:.])[:.]?\s*(?P<rest>.*)$"
)
WINDOW = 40.0
SLACK = 2.0
NEXT_PAGE_LINES = 10
HEADING_LINES = 3
_WORD = re.compile(r"[\w\xa7]+")


@dataclass(frozen=True)
class Entry:
    ordinal: int
    depth: int
    level: str
    number: str | None
    heading: str
    title: str
    page: int | None
    top: float | None

    @property
    def label(self) -> str:
        """The title without its heading, e.g. "chapter 1"."""
        if not self.heading or not self.title.endswith(self.heading):
            return self.title.strip()
        return self.title[: len(self.title) - len(self.heading)].strip(" :.\xa0")


@dataclass(frozen=True)
class Anchored:
    entry: Entry
    line: int
    heading_lines: int
    rest: str


def split(title: str) -> tuple[str, str | None, str]:
    """Split a bookmark title into (level, number, heading)."""
    clean = title.replace("\xa0", " ").strip()
    m = TITLE.match(clean)
    if not m:
        return "heading", None, clean
    level = "section" if m.group("sign") else m.group("word").lower()
    return level, m.group("number"), m.group("rest").strip()


def entries(rows: list[Row]) -> list[Entry]:
    """Return the outline in order, without repeated bookmarks."""
    out: list[Entry] = []
    last_at: dict[int, str] = {}
    for r in sorted(rows, key=lambda r: r["ordinal"]):
        depth, title = r["depth"], r["title"]
        if last_at.get(depth) == title:
            continue
        last_at = {d: t for d, t in last_at.items() if d < depth}
        last_at[depth] = title
        level, number, heading = split(title)
        out.append(Entry(r["ordinal"], depth, level, number, heading, title, r["page"], r["top"]))
    return out


def _words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text.replace("\xa0", " "))]


def _names(entry: Entry, text: str) -> bool:
    """Whether `text` has the entry's number, or its heading's first word if it has none."""
    text = text.replace("\xa0", " ")
    if entry.number:
        return bool(re.search(rf"(?<![\w.\-]){re.escape(entry.number)}(?![\w\-]|\.\w)", text))
    first = next((w for w in _words(entry.heading) if len(w) > 2), None)
    return first is not None and first in _words(text)


def _abbreviation(e: Entry) -> str:
    """Return the entry's level word if the title abbreviates it ("Ch."), else ""."""
    hit = re.match(rf"\s*{re.escape(e.level)}\.", e.title, re.IGNORECASE)
    return e.level if hit and len(e.level) > 1 else ""


def _in_title(word: str, title_words: set[str], abbreviation: str) -> bool:
    return word in title_words or bool(abbreviation and word.startswith(abbreviation))


def _rest(text: str, title_words: set[str], abbreviation: str) -> str:
    """Return `text` after its leading words that also occur in the title."""
    end = 0
    for m in _WORD.finditer(text):
        if not _in_title(m.group().lower(), title_words, abbreviation):
            break
        end = m.end()
    return text[end:].lstrip(" .:;-").strip()


def anchor(found: list[Entry], lines: list[Row], skip: Callable[[Row], bool]) -> list[Anchored]:
    """Return the entries whose heading line was found, in document order.

    `skip` marks running text. An entry found before the previous one is dropped.
    """
    by_page: dict[int, list[int]] = {}
    for i, line in enumerate(lines):
        if (line.get("text") or "").strip() and not skip(line):
            by_page.setdefault(line["page"], []).append(i)
    out: list[Anchored] = []
    after = -1
    for e in found:
        index = _find(e, lines, by_page, after)
        if index is None:
            continue
        title_words, abbreviation = set(_words(e.title)), _abbreviation(e)
        taken, rest = 1, _rest(lines[index]["text"], title_words, abbreviation)
        follow = [i for i in by_page.get(lines[index]["page"], []) if i > index]
        follow += by_page.get(lines[index]["page"] + 1, [])
        for i in follow[: HEADING_LINES - 1]:
            words = _words(lines[i]["text"])
            if rest or not words or not all(_in_title(w, title_words, abbreviation) for w in words):
                break
            taken += 1
        out.append(Anchored(e, index, taken, rest))
        after = index
    return out


def _find(e: Entry, lines: list[Row], by_page: dict[int, list[int]], after: int) -> int | None:
    if e.page is None:
        return None
    top = e.top if e.top is not None else 0.0
    page = by_page.get(e.page, [])
    for i in page:
        y = lines[i].get("y")
        near = y is not None and top - SLACK <= y <= top + WINDOW
        if i > after and near and _names(e, lines[i]["text"]):
            return i
    # A target above the page's first line points at the page as a whole.
    first_y = lines[page[0]].get("y") if page else None
    if first_y is not None and top + WINDOW < first_y:
        for i in page[:NEXT_PAGE_LINES]:
            if i > after and _names(e, lines[i]["text"]):
                return i
    for i in by_page.get(e.page + 1, [])[:NEXT_PAGE_LINES]:
        if i > after and _names(e, lines[i]["text"]):
            return i
    return None
