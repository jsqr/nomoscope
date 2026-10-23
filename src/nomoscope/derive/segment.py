"""Cut a text block into segments, as spans into the text.

A block within the limit is one segment. A longer block is cut into its
subdivisions, recursively, until each fits. A leaf that is still too long
is packed into pieces.

A level's chapeau is its text before its first child. Its closing is its
text after its last child. Each is added to the context of every segment
under it, and is not a segment itself. A chapeau over MAX_CHAPEAU_WORDS
is cut to its last sentence.

The packer splits text into paragraphs, a long paragraph into sentences,
and a long sentence at whitespace. It packs these units greedily up to
the limit. Each piece repeats whole units from the end of the previous
piece.

Token counts are estimated with CHARS_PER_TOKEN.
"""

import hashlib
import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nomoscope.derive import subdivide
from nomoscope.errors import NomoscopeError

CHARS_PER_TOKEN = 4.0

WORDS = "words"
TOKENS = "tokens"
UNITS = (WORDS, TOKENS)

WHOLE = "whole"
SUBDIVISION = "subdivision"
PACKED = "packed"

WORD = re.compile(r"\S+")
# Statute text averages about six characters per word, with the space.
# Only the packer's character budget uses this.
CHARS_PER_WORD = 6.0
OVERLAP = 0.1
MAX_CHAPEAU_WORDS = 100
ENUMERATORS = re.compile(r"^(\(\w+\)\s*)+")


class SegmentError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Limit:
    size: int = 300
    unit: str = WORDS

    def __post_init__(self) -> None:
        if self.unit not in UNITS:
            raise SegmentError(f"unknown limit unit {self.unit!r}; one of {', '.join(UNITS)}")
        if self.size < 1:
            raise SegmentError(f"limit must be positive, not {self.size}")

    def measure(self, text: str) -> int:
        if self.unit == WORDS:
            return len(text.split())
        return math.ceil(len(text) / CHARS_PER_TOKEN)

    def fits(self, text: str) -> bool:
        return self.measure(text) <= self.size

    @property
    def max_chars(self) -> int:
        per = CHARS_PER_WORD if self.unit == WORDS else CHARS_PER_TOKEN
        return int(self.size * per)

    @property
    def tag(self) -> str:
        return f"{self.size}{self.unit}"


DEFAULT_LIMIT = Limit()


@dataclass(frozen=True)
class Settings:
    """The segment limit, and whether a segment's position goes into its context."""

    limit: Limit = DEFAULT_LIMIT
    positions: bool = False

    @property
    def tag(self) -> str:
        """The settings as text, part of the derive step's code version."""
        return self.limit.tag + ("-pos" if self.positions else "")


DEFAULT = Settings()


def settings(raw: Mapping[str, Any]) -> Settings:
    """Settings from the [segments] table of the settings file."""
    limit = Limit(int(raw.get("limit", Limit.size)), str(raw.get("unit", Limit.unit)))
    return Settings(limit, bool(raw.get("positions", False)))


@dataclass(frozen=True)
class Segment:
    start: int
    end: int
    # The enumerators down to the subdivision. Empty for the whole block.
    path: tuple[str, ...]
    # Place among siblings, from 1. 0 of 0 for the whole block.
    position: int
    siblings: int
    # The chapeau of each enclosing level, outermost first.
    chapeau: tuple[str, ...]
    kind: str
    # Piece number within a packed leaf, from 1. 0 otherwise.
    piece: int = 0
    # The closing of each enclosing level, innermost first.
    closing: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return "".join(f"({p})" for p in self.path)


def cut(
    text: str,
    limit: Limit = DEFAULT_LIMIT,
    indents: Sequence[int | None] | None = None,
    divisions: Sequence[subdivide.Division] | None = None,
) -> list[Segment]:
    """The segments of text, in order.

    divisions is the text's parse, if the caller already has it.
    """
    if not text.strip():
        return []
    if limit.fits(text):
        return [Segment(0, len(text), (), 0, 0, (), WHOLE)]
    if divisions is None:
        divisions = subdivide.parse(text, indents)
    children: dict[int | None, list[subdivide.Division]] = {}
    for d in divisions:
        children.setdefault(d.parent, []).append(d)
    return list(_descend(text, limit, children, None, 0, len(text), (), 0, 0, (), ()))


def _descend(
    text: str,
    limit: Limit,
    children: Mapping[int | None, list[subdivide.Division]],
    index: int | None,
    start: int,
    end: int,
    path: tuple[str, ...],
    position: int,
    siblings: int,
    chapeau: tuple[str, ...],
    closing: tuple[str, ...],
) -> Iterator[Segment]:
    """Segments within one unit over the limit: its children, or packed pieces."""
    below = children.get(index, [])
    if not below:
        yield from _packed(text, limit, start, end, path, position, siblings, chapeau, closing)
        return
    lead = text[start : below[0].start].strip()
    if lead:
        chapeau = (*chapeau, shortened(lead))
    tail = text[below[-1].end : end].strip()
    if tail:
        closing = (tail, *closing)
    for k, child in enumerate(below, 1):
        if limit.fits(text[child.start : child.end]):
            yield Segment(
                child.start,
                child.end,
                child.path,
                k,
                len(below),
                chapeau,
                SUBDIVISION,
                0,
                closing,
            )
        else:
            yield from _descend(
                text,
                limit,
                children,
                child.sequence - 1,
                child.start,
                child.end,
                child.path,
                k,
                len(below),
                chapeau,
                closing,
            )


def shortened(lead: str) -> str:
    """Cut a long chapeau to its enumerators and its last sentence."""
    if len(WORD.findall(lead)) <= MAX_CHAPEAU_WORDS:
        return lead
    prefix = ENUMERATORS.match(lead)
    at = prefix.end() if prefix else 0
    last = sentences(lead, Span(at, len(lead)))[-1]
    return lead[:at] + lead[last.start : last.end]


def _packed(
    text: str,
    limit: Limit,
    start: int,
    end: int,
    path: tuple[str, ...],
    position: int,
    siblings: int,
    chapeau: tuple[str, ...],
    closing: tuple[str, ...],
) -> Iterator[Segment]:
    body = text[start:end]
    max_chars = limit.max_chars
    pieces = pack(units(body, max_chars), max_chars, int(max_chars * OVERLAP))
    for n, span in enumerate(pieces, 1):
        yield Segment(
            start + span.start,
            start + span.end,
            path,
            position,
            siblings,
            chapeau,
            PACKED,
            n,
            closing,
        )


def context(headings: str | None, p: Segment, *, positions: bool = False) -> str | None:
    """The text added before a segment: the heading path, each chapeau and
    closing on its own line, and the position if asked.
    """
    lines = [headings] if headings else []
    lines.extend(p.chapeau)
    lines.extend(p.closing)
    if positions and p.siblings:
        lines.append(f"{p.label} {p.position} of {p.siblings}")
    return "\n".join(lines) or None


LINE = re.compile(r"[^\n]+")

# A sentence break: end punctuation, optional closing quotes or brackets,
# whitespace, then the start of the next sentence.
BREAK = re.compile(r"(?<=[.?!])[\"')\]]*(\s+)(?=[\"'(\[]*[A-Z0-9])")

# fmt: off
ABBREVIATIONS = frozenset([
    "sec", "secs", "no", "nos", "art", "arts", "ch", "chs", "div", "divs", "subd", "subds",
    "par", "pars", "cal", "stats", "u.s", "us", "v", "vs", "e.g", "i.e", "etc", "inc", "corp",
    "co", "ltd", "dept", "govt", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec", "mr", "mrs", "ms", "dr", "st", "ave", "rev", "ed", "eds", "vol", "vols",
])
# fmt: on

WORD_BEFORE = re.compile(r"([A-Za-z0-9.]+)\.[\"')\]]*$")


@dataclass(frozen=True)
class Span:
    start: int
    end: int

    @property
    def chars(self) -> int:
        return self.end - self.start


def units(text: str, max_chars: int) -> list[Span]:
    """The pieces to pack, none longer than max_chars."""
    found = []
    for paragraph in paragraphs(text):
        if paragraph.chars <= max_chars:
            found.append(paragraph)
            continue
        for sentence in sentences(text, paragraph):
            if sentence.chars <= max_chars:
                found.append(sentence)
            else:
                found.extend(chunks(text, sentence, max_chars))
    return found


def paragraphs(text: str) -> list[Span]:
    """Non-blank lines. Subdivisions are separated by one newline."""
    return [Span(*m.span()) for m in LINE.finditer(text) if m.group().strip()]


def sentences(text: str, span: Span) -> list[Span]:
    body = text[span.start : span.end]
    cuts = [m.start(1) for m in BREAK.finditer(body) if breaks(body[: m.start(1)])]
    starts = [0, *(_skip_space(body, c) for c in cuts)]
    ends = [*cuts, len(body)]
    return [
        Span(span.start + s, span.start + e) for s, e in zip(starts, ends, strict=True) if e > s
    ]


def breaks(before: str) -> bool:
    """Whether a candidate break ends a sentence.

    A period after a digit is usually a heading (SEC. 2.) or a section number
    (65920.5.), so it does not end a sentence.
    """
    match = WORD_BEFORE.search(before)
    if match is None:
        return True
    word = match.group(1)
    return not (word[-1].isdigit() or len(word) == 1 or word.lower() in ABBREVIATIONS)


def _skip_space(body: str, index: int) -> int:
    while index < len(body) and body[index].isspace():
        index += 1
    return index


def chunks(text: str, span: Span, max_chars: int) -> list[Span]:
    """Cut a long sentence at the last whitespace that fits."""
    found = []
    start = span.start
    while span.end - start > max_chars:
        limit = start + max_chars
        cut = text.rfind(" ", start + 1, limit)
        end = cut if cut > start else limit
        found.append(Span(start, end))
        start = _skip_space(text, end)
    if span.end > start:
        found.append(Span(start, span.end))
    return found


def pack(pieces: list[Span], max_chars: int, overlap_chars: int) -> list[Span]:
    """Pack units greedily. Each segment repeats the tail of the previous one.

    The separators between units count toward a segment's size.
    """
    segments: list[Span] = []
    current: list[Span] = []
    for unit in pieces:
        if current and unit.end - current[0].start > max_chars:
            segments.append(Span(current[0].start, current[-1].end))
            current = tail(current, unit, max_chars, overlap_chars)
        current.append(unit)
    if current:
        segments.append(Span(current[0].start, current[-1].end))
    return segments


def tail(units: list[Span], unit: Span, max_chars: int, overlap_chars: int) -> list[Span]:
    """The units to repeat at the start of the next segment.

    Never all of units, so each segment adds at least one new unit.
    """
    limit = min(max_chars, unit.chars + overlap_chars)
    return [u for u in units[1:] if unit.end - u.start <= limit]


def content(context: str | None, body: str) -> str:
    """The text embedded for a segment: its context, then its span.

    Anything that rebuilds a segment's text must call this.
    """
    return f"{context}\n\n{body}" if context else body


def content_sha256(context: str | None, body: str) -> str:
    return hashlib.sha256(content(context, body).encode("utf-8")).hexdigest()
