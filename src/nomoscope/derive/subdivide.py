"""Parse a section's text into subdivisions.

A subdivision starts with an enumerator in parentheses at the start of a
line: (a), (1), (A), (i). One line can start several, as in "(c) (1)".

The text does not state depth. The parser infers it from the
subdivisions still open: (b) after (a) is a sibling, and (1) after (a) is
a child. If the source gives each line's indent, the indent decides the
depth.

Some enumerators are ambiguous. (i) can be a roman numeral or the letter
after (h), and likewise (v), (x), (c), and (ii) after (hh). The parser
prefers the reading that continues an open subdivision, then the one that
starts a new sequence.

A line without an enumerator belongs to the innermost open subdivision,
with one exception. Suppose every item of a list is one line except the
last, and the last item's extra lines start with a capital letter. Then
those lines close the list and belong to its parent. Text before the
first enumerator belongs to no subdivision.

Level words come from the drafting conventions. Most conventions rank a
subdivision by its enumerator style, and some by its indent. Without
conventions, levels are null.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

LOWER_ALPHA = "lower_alpha"
DIGIT = "digit"
UPPER_ALPHA = "upper_alpha"
LOWER_ROMAN = "lower_roman"
UPPER_ROMAN = "upper_roman"

STYLE_RANK = {LOWER_ALPHA: 1, DIGIT: 2, UPPER_ALPHA: 3, LOWER_ROMAN: 4, UPPER_ROMAN: 5}


@dataclass(frozen=True)
class Levels:
    """A convention's level words, by rank.

    indent, if set, is (first indent, step): lines are ranked by indent
    where the source gives one.
    """

    words: tuple[str, ...]
    indent: tuple[int, int] | None = None


TOKEN_RE = re.compile(r"\(([A-Za-z]{1,6}|\d{1,3})(?:-(\d+))?\)")
# Some sources start the text with the number and heading, before (a):
# "Sec. 1.002.  PURPOSE.  (a) The purpose ..."
RUN_IN_RE = re.compile(r"^Sec\.\s*\S+\s+(?:[^.\n]*?\.\s+)?(?=\()")
ROMAN_RE = re.compile(r"^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$")
ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}


@dataclass(frozen=True)
class Division:
    path: tuple[str, ...]
    style: str
    number: str
    depth: int
    start: int
    end: int
    parent: int | None
    position: int
    sequence: int
    indent: int | None
    level: str | None = None


def level(levels: Levels | None, division: Division) -> str | None:
    """The source's word for the division's level, else the convention's, or None."""
    if division.level is not None:
        return division.level
    if levels is None:
        return None
    if levels.indent is not None and division.indent is not None:
        first, step = levels.indent
        rank = (division.indent - first) // step + 1
    else:
        rank = STYLE_RANK[division.style]
    return levels.words[rank - 1] if 0 < rank <= len(levels.words) else None


@dataclass(frozen=True)
class Enumerator:
    style: str
    ordinal: int
    sub: int | None = None

    @property
    def first(self) -> bool:
        return self.ordinal == 1 and self.sub is None


def roman(letters: str) -> int | None:
    upper = letters.upper()
    if not upper or not ROMAN_RE.match(upper):
        return None
    total = 0
    for here, after in zip(upper, upper[1:] + " ", strict=True):
        value = ROMAN_VALUES[here]
        total += -value if ROMAN_VALUES.get(after, 0) > value else value
    return total


def candidates(number: str) -> list[Enumerator]:
    """The readings of an enumerator, alphabetic before roman."""
    base, _, suffix = number.partition("-")
    sub = int(suffix) if suffix else None
    if base.isdigit():
        return [Enumerator(DIGIT, int(base), sub)]
    lower = base.islower()
    found = []
    if len(base) == 1 or (len(base) == 2 and base[0] == base[1]):
        ordinal = (len(base) - 1) * 26 + ord(base[0].lower()) - ord("a") + 1
        found.append(Enumerator(LOWER_ALPHA if lower else UPPER_ALPHA, ordinal, sub))
    value = roman(base)
    if value is not None:
        found.append(Enumerator(LOWER_ROMAN if lower else UPPER_ROMAN, value, sub))
    return found


def succeeds(prev: Enumerator, cur: Enumerator) -> bool:
    """Whether b follows a. A suffix continues its base: a, a-1, a-2, b."""
    if prev.style != cur.style:
        return False
    if cur.sub is None:
        return cur.ordinal == prev.ordinal + 1
    return cur.ordinal == prev.ordinal and cur.sub == (prev.sub or 0) + 1


def tokens(line: str) -> list[tuple[str, int, list[Enumerator]]]:
    """The enumerators at the start of a line, with offsets and readings."""
    m = RUN_IN_RE.match(line)
    at = m.end() if m else 0
    found = []
    while (m := TOKEN_RE.match(line, at)) is not None and (readings := candidates(m[0][1:-1])):
        found.append((m[0][1:-1], m.start(), readings))
        at = m.end()
        while at < len(line) and line[at].isspace():
            at += 1
    return found


@dataclass
class Frame:
    index: int
    enum: Enumerator
    indent: int | None


@dataclass
class Opened:
    path: tuple[str, ...]
    style: str
    number: str
    depth: int
    start: int
    parent: int | None
    position: int
    indent: int | None


def parse(text: str, indents: Sequence[int | None] | None = None) -> list[Division]:
    """The subdivisions of a section's text, in order, as spans.

    indents has one value per line, if the source gives them.
    """
    opened: list[Opened] = []
    stack: list[Frame] = []
    seen: set[tuple[str, ...]] = set()
    children: dict[int | None, int] = {}
    offset = 0
    for lineno, line in enumerate(text.split("\n")):
        indent = indents[lineno] if indents is not None and lineno < len(indents) else None
        for k, (number, at, found) in enumerate(tokens(line)):
            if k == 0 and indent is not None:
                keep, enum = by_indent(stack, indent, found)
            else:
                keep, enum = by_succession(stack, found)
            parent = keep[-1].index if keep else None
            children[parent] = children.get(parent, 0) + 1
            path = unique(
                (*opened[parent].path, number) if parent is not None else (number,),
                seen,
            )
            seen.add(path)
            depth, start, position = len(keep) + 1, offset + at, children[parent]
            opened.append(Opened(path, enum.style, number, depth, start, parent, position, indent))
            stack = [*keep, Frame(len(opened) - 1, enum, indent)]
        offset += len(line) + 1
    return close(opened, text)


def by_indent(
    stack: list[Frame], indent: int, found: list[Enumerator]
) -> tuple[list[Frame], Enumerator]:
    """Close the frames at or beyond the token's indent. The token follows the
    last frame at its indent, or is a child of the top frame.
    """
    keep = [f for f in stack if f.indent is None or f.indent < indent]
    closed = [f for f in stack if f.indent is not None and f.indent == indent]
    if closed:
        prev = closed[-1].enum
        for enum in found:
            if succeeds(prev, enum):
                return keep, enum
    return keep, next((e for e in found if e.first), found[0])


def by_succession(stack: list[Frame], found: list[Enumerator]) -> tuple[list[Frame], Enumerator]:
    """Place a token by succession, trying the deepest frame first.

    1. A reading that continues a frame makes the token that frame's sibling.
    2. Else a first value (a, 1, A, i) is a child of the top frame, or
       replaces the top frame if it has the same style.
    3. Else the token replaces a frame of its style.
    4. Else it is a child of the top frame.
    """
    for depth in range(len(stack) - 1, -1, -1):
        for enum in found:
            if succeeds(stack[depth].enum, enum):
                return stack[:depth], enum
    for enum in found:
        if enum.first:
            if stack and stack[-1].enum.style == enum.style:
                return stack[:-1], enum
            return stack, enum
    for depth in range(len(stack) - 1, -1, -1):
        for enum in found:
            if stack[depth].enum.style == enum.style:
                return stack[:depth], enum
    return stack, found[0]


def unique(path: tuple[str, ...], seen: set[tuple[str, ...]]) -> tuple[str, ...]:
    """Add a ~n suffix to a path already used in this section."""
    if path not in seen:
        return path
    n = 2
    while (*path[:-1], f"{path[-1]}~{n}") in seen:
        n += 1
    return (*path[:-1], f"{path[-1]}~{n}")


def close(opened: list[Opened], text: str) -> list[Division]:
    """End each span at the next subdivision that is not its descendant,
    without trailing whitespace. A list's closing lines are cut from its
    last item.
    """
    ends = [len(text)] * len(opened)
    # For each item, the next item at the same depth or shallower. A stack
    # finds them in one pass.
    pending: list[Opened] = []
    for i in range(len(opened) - 1, -1, -1):
        d = opened[i]
        while pending and pending[-1].depth > d.depth:
            pending.pop()
        if pending:
            ends[i] = pending[-1].start
        pending.append(d)
    children: dict[int | None, list[int]] = {}
    for i, d in enumerate(opened):
        children.setdefault(d.parent, []).append(i)
    for i in range(len(opened)):
        cut = closing(opened, ends, text, i, children)
        if cut is not None:
            ends[i] = cut
    out = []
    for i, d in enumerate(opened):
        end = ends[i]
        while end > d.start and text[end - 1].isspace():
            end -= 1
        out.append(Division(**vars(d), end=end, sequence=i + 1))
    return out


def closing(
    opened: list[Opened],
    ends: list[int],
    text: str,
    i: int,
    children: dict[int | None, list[int]],
) -> int | None:
    """Where a list's last item ends, if its extra lines close the list.

    That applies when the item is a leaf, every other item is one line, and
    the first extra line starts with a capital letter. Otherwise None.
    """
    d = opened[i]
    siblings = children[d.parent]
    if siblings[-1] != i or len(siblings) < 2:
        return None
    if children.get(i):
        return None
    if any("\n" in text[opened[j].start : ends[j]].strip() for j in siblings[:-1]):
        return None
    first_break = text.find("\n", d.start, ends[i])
    if first_break < 0:
        return None
    rest = text[first_break + 1 : ends[i]].lstrip()
    if not rest or not rest[0].isupper():
        return None
    return first_break


# The structure grammar's style names, as this module names them.
TABULATED_STYLES = {"arabic": DIGIT}
RUN_IN = "run_in"


def from_tabulated(rows: Sequence[Mapping[str, Any]]) -> list[Division]:
    """Divisions from a tabulated `subdivisions` list, in text order."""
    ordered = sorted(rows, key=lambda r: r["start_char"])
    index: dict[tuple[str, ...], int] = {}
    children: dict[int | None, int] = {}
    out = []
    for i, r in enumerate(ordered):
        parts: list[str] = r["path"].split("/")
        path = tuple(parts)
        parent = index.get(path[:-1]) if len(path) > 1 else None
        children[parent] = children.get(parent, 0) + 1
        index[path] = i
        raw: str | None = r["style"]
        style = RUN_IN if raw is None else TABULATED_STYLES.get(raw, raw)
        out.append(
            Division(
                path=path,
                style=style,
                number=r["number"] or parts[-1],
                depth=len(path),
                start=r["start_char"],
                end=r["end_char"],
                parent=parent,
                position=children[parent],
                sequence=i + 1,
                indent=None,
                level=r["level"],
            )
        )
    return out
