"""Segment settings: how long a segment of section text may be, and whether
its position goes into its context. A project sets them in its registry;
`derive/segment.py` cuts text with them.
"""

import math
from dataclasses import dataclass

from nomoscope.errors import NomoscopeError

CHARS_PER_TOKEN = 4.0

WORDS = "words"
TOKENS = "tokens"
UNITS = (WORDS, TOKENS)

# Statute text averages about six characters per word, with the space.
# Only the packer's character budget uses this.
CHARS_PER_WORD = 6.0


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
