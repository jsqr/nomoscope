"""Drafting conventions: the date phrases of history notes, and the words for
subdivision levels.

Each set of conventions is a file <drafting>.toml in the project's
conventions directory (`Registry.conventions`), where <drafting> is a
value of the drafting column of law_sections:

    [history]
    format = "numeric_mdy"    # a parser in history.FORMATS
    which = "last"            # first, last, or latest
    note = 'Note:'            # optional: text after it is ignored
    effective = '\\beff\\. {NUMERIC_DATE}'
    repeal = '\\bRepealed by Ord\\. [\\w-]+, eff\\. {NUMERIC_DATE}'
    enacted_year = ', {YEAR}\\)'   # optional: for notes that give only a year

    [levels]
    words = ["subsection", "paragraph", "subparagraph"]
    indent = [7, 6]           # optional: rank lines by indent

Each date phrase is a regular expression. {MONTH_DATE} and {NUMERIC_DATE}
are replaced by patterns that capture the date, and {YEAR} by one that
captures a year. Either table is optional.
"""

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nomoscope.derive import history
from nomoscope.derive.subdivide import Levels
from nomoscope.errors import NomoscopeError

# The scope of the conventions input of a derive step.
SCOPE = "conventions"
PLACEHOLDERS = {
    "{MONTH_DATE}": history.MONTH_DATE,
    "{NUMERIC_DATE}": history.NUMERIC_DATE,
    "{YEAR}": history.YEAR,
}
WHICH = ("first", "last", "latest")
CONTROLS = ("format", "which", "note")


class ConventionsError(NomoscopeError):
    pass


@dataclass(frozen=True)
class Conventions:
    phrases: Mapping[str, Mapping[str, str]] = field(default_factory=dict)
    levels: Mapping[str, Levels] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        data = {
            "phrases": self.phrases,
            "levels": {k: [list(v.words), v.indent] for k, v in self.levels.items()},
        }
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


NONE = Conventions()


def phrases(name: str, raw: Mapping[str, Any]) -> dict[str, str]:
    unknown = set(raw) - {*CONTROLS, *history.ASPECTS, *history.YEARS}
    if unknown:
        raise ConventionsError(f"{name}: unknown history keys {sorted(unknown)}")
    if raw.get("format") not in history.FORMATS:
        raise ConventionsError(f"{name}: format must be one of {sorted(history.FORMATS)}")
    if raw.get("which") not in WHICH:
        raise ConventionsError(f"{name}: which must be one of {', '.join(WHICH)}")
    found = {}
    for key, value in raw.items():
        if not isinstance(value, str):
            raise ConventionsError(f"{name}: {key} must be a string")
        for token, pattern in PLACEHOLDERS.items():
            value = value.replace(token, pattern)
        if key in (*history.ASPECTS, *history.YEARS):
            try:
                compiled = re.compile(value)
            except re.error as e:
                raise ConventionsError(f"{name}: {key}: {e}") from e
            group = "year" if key in history.YEARS else "date"
            if group not in compiled.groupindex:
                raise ConventionsError(f"{name}: {key} captures no {group}")
        found[key] = value
    return found


def levels(name: str, raw: Mapping[str, Any]) -> Levels:
    words = raw.get("words")
    if not words or not all(isinstance(w, str) for w in words):
        raise ConventionsError(f"{name}: levels.words must be a list of words")
    indent = raw.get("indent")
    if indent is not None and (len(indent) != 2 or not all(isinstance(n, int) for n in indent)):
        raise ConventionsError(f"{name}: levels.indent must be [first, step]")
    return Levels(tuple(words), tuple(indent) if indent is not None else None)


def parse(files: Mapping[str, Mapping[str, Any]]) -> Conventions:
    """Build conventions from parsed TOML, by drafting name."""
    found_phrases, found_levels = {}, {}
    for name, raw in sorted(files.items()):
        unknown = set(raw) - {"history", "levels"}
        if unknown:
            raise ConventionsError(f"{name}: unknown tables {sorted(unknown)}")
        if "history" in raw:
            found_phrases[name] = phrases(name, raw["history"])
        if "levels" in raw:
            found_levels[name] = levels(name, raw["levels"])
    return Conventions(found_phrases, found_levels)


def load(directory: Path | None) -> Conventions:
    """The project's conventions. Empty without a conventions directory."""
    if directory is None or not directory.is_dir():
        return NONE
    files = {}
    for path in sorted(directory.glob("*.toml")):
        try:
            files[path.stem] = tomllib.loads(path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConventionsError(f"{path}: {e}") from e
    return parse(files)
