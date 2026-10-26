"""Read label tokens such as 12, C, and iv, and decide which follows which.

An arabic number may carry a decimal insertion (52.1 falls between 52
and 53); letters double past z (aa is 27). A caller tries each style a
token could be in: (i) is a letter and a roman numeral.
"""

import re
from dataclasses import dataclass
from typing import Literal

Style = Literal["arabic", "upper_alpha", "lower_alpha", "upper_roman", "lower_roman"]
STYLES: tuple[Style, ...] = ("arabic", "upper_alpha", "lower_alpha", "upper_roman", "lower_roman")

PATTERNS: dict[Style, str] = {
    "arabic": r"\d+",
    "upper_alpha": r"[A-Z]{1,2}",
    "lower_alpha": r"[a-z]{1,2}",
    "upper_roman": r"[IVXLCDM]+",
    "lower_roman": r"[ivxlcdm]+",
}
DECIMAL = r"(?:\.\d+)?"

ROMAN = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
_ROMAN_FORM = re.compile(r"m{0,3}(cm|cd|d?c{0,3})(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")


@dataclass(frozen=True)
class Enumerator:
    style: Style
    ordinal: int
    sub: int = 0

    @property
    def first(self) -> bool:
        return self.ordinal == 1 and self.sub == 0


def pattern(style: Style, decimal: bool = False) -> str:
    """Return the regex fragment for one token of `style`."""
    return PATTERNS[style] + (DECIMAL if decimal and style == "arabic" else "")


def _roman(token: str) -> int | None:
    t = token.lower()
    if not t or not _ROMAN_FORM.fullmatch(t):
        return None
    values = [ROMAN[c] for c in t]
    return sum(-v if i + 1 < len(values) and v < values[i + 1] else v for i, v in enumerate(values))


def _alpha(token: str) -> int | None:
    t = token.lower()
    if len(t) == 1:
        return ord(t) - ord("a") + 1
    if len(t) == 2 and t[0] == t[1]:
        return 26 + ord(t[0]) - ord("a") + 1
    return None


def read(token: str, style: Style) -> Enumerator | None:
    """Read `token` as an enumerator of `style`, or return None."""
    if not re.fullmatch(pattern(style, decimal=True), token):
        return None
    if style == "arabic":
        whole, _, frac = token.partition(".")
        return Enumerator(style, int(whole), int(frac) if frac else 0)
    ordinal = _alpha(token) if style.endswith("alpha") else _roman(token)
    return Enumerator(style, ordinal) if ordinal else None


def succeeds(prev: Enumerator, cur: Enumerator) -> bool:
    """Whether `cur` is the next label after `prev`."""
    if prev.style != cur.style:
        return False
    if cur.ordinal == prev.ordinal + 1 and cur.sub == 0:
        return True
    return cur.ordinal == prev.ordinal and cur.sub == prev.sub + 1


def increases(prev: Enumerator, cur: Enumerator) -> bool:
    return prev.style == cur.style and (cur.ordinal, cur.sub) > (prev.ordinal, prev.sub)


_LEADING_ZEROS = re.compile(r"(?<!\d)0+(?=\d)")


def same_number(a: str | None, b: str | None) -> bool:
    """Whether two printed numbers are equal, as "01" and "1" are."""
    return (
        a is not None and b is not None and _LEADING_ZEROS.sub("", a) == _LEADING_ZEROS.sub("", b)
    )
