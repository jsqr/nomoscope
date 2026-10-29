"""Finding the model's verbatim quotes in a unit.

Quote marks are straightened and whitespace collapsed before an exact
search. Failing that, the best-matching window near the quote's length is
taken if its similarity is at least FUZZY.
"""

import difflib
from collections.abc import Sequence

from nomoscope.derive import segment

FUZZY = 0.8
# A quote with a dropped or added letter differs in length from its match.
SLACK = 2
QUOTE_MARKS = str.maketrans({0x201C: '"', 0x201D: '"', 0x2018: "'", 0x2019: "'", 0xA0: " "})


def normalized(text: str) -> tuple[str, list[int]]:
    """Also returns, for each output offset, its offset in `text`."""
    out: list[str] = []
    back: list[int] = []
    for i, ch in enumerate(text.translate(QUOTE_MARKS)):
        if ch.isspace():
            if out and out[-1] == " ":
                continue
            ch = " "
        out.append(ch)
        back.append(i)
    return "".join(out), back


def locate(text: str, quote: str, *, accept: float = FUZZY) -> tuple[int, int] | None:
    norm, back = normalized(text)
    q = normalized(quote)[0].strip()
    if not q:
        return None
    at = norm.find(q)
    if at >= 0:
        return back[at], back[at + len(q) - 1] + 1
    matcher = difflib.SequenceMatcher(None, norm, q, autojunk=False)
    block = matcher.find_longest_match(0, len(norm), 0, len(q))
    if block.size == 0:
        return None
    start = max(0, min(block.a - block.b, len(norm) - len(q)))
    lengths = range(max(1, len(q) - SLACK), len(q) + SLACK + 1)
    ratio, end = max(
        (difflib.SequenceMatcher(None, norm[start:end], q, autojunk=False).ratio(), end)
        for end in {min(len(norm), start + n) for n in lengths}
    )
    if ratio < accept:
        return None
    return trimmed(text, back[start], back[end - 1] + 1)


def trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def located(context: str | None, body: str, quotes: Sequence[str]) -> list[tuple[int, int] | None]:
    """Spans in `body`, or None for a quote not found there.

    The body is searched before the context, which can repeat it. A repeated
    quote is searched for after its previous match, so "this chapter" cited
    three times gets three spans, and a fourth copy is a duplicate. A quote
    found only in the context belongs to another unit and gets None.
    """
    norm, back = normalized(body)
    after: dict[str, int] = {}
    content = segment.content(context, body)
    offset = len(content) - len(body)
    found: list[tuple[int, int] | None] = []
    for quote in quotes:
        q = normalized(quote)[0].strip()
        at = norm.find(q, after.get(q, 0)) if q else -1
        if at >= 0:
            after[q] = at + len(q)
            found.append((back[at], back[at + len(q) - 1] + 1))
            continue
        if q in after:
            found.append(None)
            continue
        span = locate(body, quote)
        if span is None:
            whole = locate(content, quote)
            if whole is not None and whole[1] > offset:
                span = (max(whole[0], offset) - offset, whole[1] - offset)
        if span is not None:
            after[q] = span[1]
        found.append(span)
    return found
