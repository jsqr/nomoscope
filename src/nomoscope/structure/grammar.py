"""The grammar of one code: its levels, their label templates, and layout signals.

In a label template, `{n}` is the level's own number and `{<level>}`
an enclosing level's number; other characters are literal. `compile`
turns a template into a regex that allows whitespace between pieces,
because text layers often space out punctuation.

`run_in` describes unnumbered paragraphs that start with a phrase, such
as a defined term. `sections` says which paragraphs are history and
which are publisher's notes. A person's acceptance of a parsed code is
kept apart from its grammar, in the reviews tables (`crunch/reviews.py`).
"""

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import tomli_w
from pydantic import BaseModel, Field, field_validator, model_validator

from nomoscope.structure import enumerate as en

FIELD = re.compile(r"\{(\w+)\}")


def _decoded(value):
    """Decode a nested object that a model sent as a JSON string."""
    return json.loads(value) if isinstance(value, str) else value


def _regex(value: str) -> str:
    # re.error is not a ValueError, which pydantic needs to report it.
    try:
        re.compile(value)
    except re.error as e:
        raise ValueError(f"bad regex {value!r}: {e}") from e
    return value


def _regexes(values: list[str]) -> list[str]:
    for v in values:
        _regex(v)
    return values


class Signals(BaseModel):
    """Layout features a label line must have. A feature the line lacks is not checked."""

    bold: bool | None = Field(default=None, description="Line is mostly bold")
    italic: bool | None = Field(default=None, description="Line is mostly italic")
    caps: bool | None = Field(default=None, description="Line is all capitals")
    centered: bool | None = Field(default=None, description="Line is centered on the page")
    size_min: float | None = Field(default=None, description="Smallest font size, points")
    size_max: float | None = Field(default=None, description="Largest font size, points")
    x: float | None = Field(
        default=None, description="Left edge of the line, points from the page edge"
    )
    x_tolerance: float = Field(default=4.0, description="Allowed distance from x, points")
    gap_min: float | None = Field(
        default=None, description="Smallest vertical distance from the previous line, points"
    )


class Level(BaseModel):
    name: str = Field(description="The code's own word for the level, lowercase singular")
    kind: Literal["division", "subdivision"] = Field(
        description="division: a heading line such as a chapter or section; "
        "subdivision: an enumerated paragraph within a section"
    )
    label: str = Field(
        description="Label template: {n} for this level's enumerator, {<level>} for the "
        "number of an enclosing level, other characters literal, e.g. 'ARTICLE {n}', "
        "'\u00a7 {chapter}-{n}.', '({n})'"
    )
    style: en.Style = Field(description="Enumerator style of {n}")
    decimal: bool = Field(
        default=False, description="Arabic numbers may carry inserted decimals, 52.1"
    )
    sequence: Literal["consecutive", "increasing", "none"] = Field(
        default="consecutive",
        description="How siblings are numbered: each one more than the last, "
        "increasing with gaps, or no order",
    )
    heading: Literal["same_line", "next_line", "none"] = Field(
        default="none", description="Where the heading text is, relative to the label"
    )
    signals: Signals = Signals()
    spaced: bool = Field(
        default=True,
        description="Allow whitespace between the label's pieces, as text layers print "
        "'A .'; false requires them to touch where the template has no space, so "
        "'I . . . do solemnly swear' is not label 'I.'",
    )

    _signals = field_validator("signals", mode="before")(_decoded)

    @model_validator(mode="after")
    def _one_n(self):
        if FIELD.findall(self.label).count("n") != 1:
            raise ValueError(f"label {self.label!r} must contain {{n}} exactly once")
        return self


class Skip(BaseModel):
    top: float | None = Field(
        default=None,
        description="Lines with y above this are the running header; points from the top",
    )
    bottom: float | None = Field(
        default=None,
        description="Lines with y below this are the running footer; points from the top",
    )
    block_types: list[str] = Field(
        default=["header", "footer"], description="Block types dropped as running text"
    )
    toc_block_types: list[str] = Field(
        default=["table"], description="Block types read as body text that start no node"
    )
    note_block_types: list[str] = Field(
        default=["references"], description="Block types kept as notes of the open node"
    )
    note_size_max: float | None = Field(
        default=None,
        description="Lines in this font size or smaller, such as footnotes, are kept as "
        "notes of the open node, not text; points",
    )

    @model_validator(mode="after")
    def _bands(self):
        if self.top is not None and self.bottom is not None and self.bottom <= self.top:
            raise ValueError(f"bottom {self.bottom} must be below top {self.top}")
        return self


class Form(BaseModel):
    form: str = Field(
        description="The phrase that starts a run-in paragraph: {lead} once, other "
        "characters literal, e.g. '{lead} \u2014', '{lead}.'"
    )
    caps: bool = Field(default=False, description="The phrase starts with a word in capitals")
    bold: bool = Field(default=False, description="The form is a whole bold run of the line")
    followed_by: str | None = Field(
        default=None,
        description="Text that must follow the form, and stays in the paragraph, e.g. "
        "'Means' for 'City Official. Means ...'",
    )

    @model_validator(mode="after")
    def _one_lead(self):
        if FIELD.findall(self.form) != ["lead"]:
            raise ValueError(f"form {self.form!r} must contain {{lead}} and no other field")
        return self


class RunIn(BaseModel):
    within: str = Field(
        default=r"(?i)\bdefinitions?\b",
        description="Regex that the heading of a division with run-in paragraphs matches",
    )
    forms: list[Form] = Field(description="The forms of the starting phrase, tried in order")

    _within = field_validator("within")(_regex)


class Sections(BaseModel):
    """Rules that split a section's paragraphs into text, history, and dropped notes."""

    history: list[str] = Field(
        default=[],
        description="Regexes; the paragraphs at the end of a section that match one are "
        "its history note, e.g. '^\\(Ord\\. '",
    )
    history_tail: list[str] = Field(
        default=[],
        description="Regexes; a match at the end of the section's last paragraph is "
        "its history note, printed on the same line as the text, e.g. "
        "'\\s+\\(Ord\\. [^()]*\\)$'",
    )
    drop: list[str] = Field(
        default=[],
        description="Regexes; a paragraph that matches one is a publisher's note and is "
        'left out, e.g. "^Editor\'s note"',
    )
    drafting: str | None = Field(
        default=None, description="The drafting conventions of the sections, by file name"
    )
    hyphens: Literal["keep", "syllables", "compounds"] = Field(
        default="keep",
        description="Words broken at a line end ('autho-' then 'rized'): keep them as "
        "printed, or rejoin them. A rejoined word loses the hyphen if it occurs "
        "unbroken elsewhere in the document, and keeps it if it occurs hyphenated "
        "mid-line ('on-site'); otherwise 'syllables' drops it (justified text breaks "
        "words anywhere) and 'compounds' keeps it (text that breaks only at hyphens)",
    )
    level_names: Literal["style", "depth"] = Field(
        default="style",
        description="How subdivisions are named: by the level their enumerator style "
        "matches, or by their depth below the section, using the subdivision levels' "
        "names in order (for codes whose sections number their levels differently)",
    )
    levels: list[str] = Field(
        default=[],
        description="Division levels above the section that are tabulated as sections when "
        "no division is under them, such as a definitions chapter without sections",
    )

    _history = field_validator("history")(_regexes)
    _history_tail = field_validator("history_tail")(_regexes)
    _drop = field_validator("drop")(_regexes)


class Correction(BaseModel):
    """A line the document misprints, as the parse should read it.

    Only the parse reads the correction: the text keeps the print.
    """

    page: int = Field(description="The page, counting from 1")
    printed: str = Field(description="The start of the line as printed, e.g. 'b. Authority.'")
    reads: str = Field(description="What the parse reads in its place, e.g. 'B. Authority.'")


class Grammar(BaseModel):
    levels: list[Level] = Field(description="Divisions from the top down, then subdivisions")
    skip: Skip = Skip()
    bookmarks: bool = Field(
        default=True, description="Take divisions from the document's bookmarks where it has them"
    )
    run_in: RunIn | None = Field(
        default=None,
        description="Paragraphs that start with an unnumbered phrase, such as a defined "
        "term, and hold the lettered items after them",
    )
    sections: Sections = Sections()
    corrections: list[Correction] = Field(
        default=[], description="Misprinted labels, such as 'b.' among A, B, C"
    )

    _skip = field_validator("skip", mode="before")(_decoded)
    _run_in = field_validator("run_in", mode="before")(_decoded)
    _sections = field_validator("sections", mode="before")(_decoded)

    @model_validator(mode="before")
    @classmethod
    def _no_review(cls, data):
        if isinstance(data, dict) and "review" in data:
            raise ValueError(
                "[review] is no longer part of a grammar; remove it, and record the review "
                "with `grammar accept`"
            )
        return data

    @model_validator(mode="after")
    def _levels(self):
        names = [lv.name for lv in self.levels]
        kinds = [lv.kind for lv in self.levels]
        first_sub = next((i for i, k in enumerate(kinds) if k == "subdivision"), len(kinds))
        if "division" in kinds[first_sub:]:
            raise ValueError("every division must come before the first subdivision")
        if len(set(names)) != len(names):
            raise ValueError(f"level names repeat: {names}")
        for i, lv in enumerate(self.levels):
            for ref in set(FIELD.findall(lv.label)) - {"n"}:
                if ref not in names[:i]:
                    raise ValueError(f"{lv.name}: {{{ref}}} names no level above it")
        divisions = [lv.name for lv in self.levels if lv.kind == "division"]
        unknown = set(self.sections.levels) - set(divisions)
        if unknown:
            raise ValueError(f"sections.levels {sorted(unknown)} are not division levels")
        return self


def loads(text: str) -> Grammar:
    return Grammar.model_validate(tomllib.loads(text))


def load(path: Path) -> Grammar:
    return loads(path.read_text(encoding="utf-8"))


def dumps(grammar: Grammar, comment: str = "") -> str:
    head = "".join(f"# {line}\n" for line in comment.splitlines())
    body = tomli_w.dumps(grammar.model_dump(exclude_none=True, exclude_defaults=True))
    # Non-ASCII only occurs inside strings, where TOML accepts \u escapes.
    return head + "".join(c if ord(c) < 128 else f"\\u{ord(c):04x}" for c in body)


@dataclass(frozen=True)
class Matcher:
    level: Level
    regex: re.Pattern[str]
    anywhere: re.Pattern[str]


@dataclass(frozen=True)
class Match:
    level: Level
    enum: en.Enumerator
    number: str
    label: str
    refs: dict[str, str]
    rest: str


def _piece(tok: str, level: Level, optional: bool) -> str:
    field = FIELD.fullmatch(tok)
    if field and field.group(1) == "n":
        return f"(?P<n>{en.pattern(level.style, level.decimal)})"
    if field:
        # Greedy, so "2.12.070" reads as chapter 2.12, number 070.
        return rf"(?P<ref_{field.group(1)}>[\w.]+)"
    if optional and not tok[0].isalnum():
        return f"(?:{re.escape(tok)})?"
    return f"(?i:{re.escape(tok)})" if tok[0].isalpha() else re.escape(tok)


def compile(level: Level, loose: bool = False) -> Matcher:
    """Return a matcher for `level`'s label at the start of a line.

    `loose` makes punctuation after the last field optional, for matching bookmark titles.
    A level that is not `spaced` allows whitespace only where its template has a space.
    """
    toks: list[str] = []
    gaps: list[bool] = []  # whether the template has a space before each piece
    gap = False
    for part in re.split(r"(\{\w+\})", level.label):
        pieces = [part] if FIELD.fullmatch(part) else re.findall(r"\s+|\w+|[^\w\s]", part)
        for tok in pieces:
            if tok.isspace():
                gap = True
                continue
            toks.append(tok)
            gaps.append(gap)
            gap = False
    last = max(i for i, t in enumerate(toks) if FIELD.fullmatch(t))
    tight = not level.spaced and not loose
    body = "".join(
        (("\\s+" if gaps[i] else "") if tight else ("\\s*" if i else ""))
        + _piece(t, level, loose and i > last)
        for i, t in enumerate(toks)
    )
    tail = r"(?![\w])" if toks[-1][-1].isalnum() or FIELD.fullmatch(toks[-1]) else ""
    return Matcher(
        level,
        re.compile(rf"^\s*(?P<label>{body}){tail}\s*"),
        re.compile(rf"(?<![\w])(?P<label>{body}){tail}"),
    )


def matchers(grammar: Grammar, loose: bool = False) -> list[Matcher]:
    return [compile(lv, loose) for lv in grammar.levels]


def match(m: Matcher, text: str) -> Match | None:
    hit = m.regex.match(text)
    if not hit:
        return None
    enum = en.read(hit.group("n"), m.level.style)
    if enum is None:
        return None
    groups = [g for g in m.regex.groupindex if g == "n" or g.startswith("ref_")]
    spans = [hit.span(g) for g in groups]
    number = text[min(s for s, _ in spans) : max(e for _, e in spans)]
    refs = {g[4:]: hit.group(g) for g in groups if g.startswith("ref_")}
    label = hit.group("label")
    return Match(m.level, enum, re.sub(r"\s+", "", number), label, refs, text[hit.end() :])


def numbers(m: Matcher, text: str) -> list[str]:
    """Return the numbers of every label of `m`'s level in `text`."""
    out = []
    for hit in m.anywhere.finditer(text):
        groups = [g for g in m.anywhere.groupindex if g == "n" or g.startswith("ref_")]
        spans = [hit.span(g) for g in groups]
        out.append(re.sub(r"\s+", "", text[min(s for s, _ in spans) : max(e for _, e in spans)]))
    return out


def classify(text: str, ms: list[Matcher]) -> Match | None:
    """Return the match of the first level whose label starts `text`."""
    for m in ms:
        if found := match(m, text):
            return found
    return None


QUOTES = "\"'\u201c\u201d\u2018\u2019"
DASHES = "\u2014\u2013-"
LEAD_CHARS = 100


@dataclass(frozen=True)
class Lead:
    text: str
    end: int


def _form_piece(tok: str) -> str:
    if all(c in QUOTES for c in tok):
        return f"[{re.escape(QUOTES)}]"
    if all(c in DASHES for c in tok):
        return f"[{DASHES}]{{1,2}}"
    return f"(?i:{re.escape(tok)})" if tok[0].isalpha() else re.escape(tok)


def compile_form(form: Form) -> re.Pattern[str]:
    """Return a regex for `form`, in which any quote matches any quote and any dash any dash.

    A space in the form requires whitespace, so "{lead} \u2014" does not
    match a hyphenated word.
    """
    out, spaced = r"\s*", True
    for tok in re.findall(r"\{lead\}|\s+|\w+|[^\w\s]", form.form):
        if tok.isspace():
            out, spaced = out + r"\s+", True
            continue
        if not spaced:
            out += r"\s*"
        lead = rf"(?P<lead>[^\W\d_].{{0,{LEAD_CHARS - 1}}}?)"
        out, spaced = out + (lead if tok == "{lead}" else _form_piece(tok)), False
    if form.followed_by:
        out += rf"(?=\s*{re.escape(form.followed_by)})"
    return re.compile(out)


def match_lead(
    form: Form, regex: re.Pattern[str], text: str, start: int, bold_spans: list[dict[str, int]]
) -> Lead | None:
    """Return the phrase `form` reads at `start` in `text`, and where it ends.

    A bold form must be a whole bold run.
    """
    at = len(text) - len(text[start:].lstrip())
    if form.bold:
        run = next((r for r in bold_spans if r["start"] <= at < r["end"]), None)
        if run is None:
            return None
        end = len(text[: run["end"]].rstrip())
        hit = regex.fullmatch(text, at, end)
    else:
        hit = regex.match(text, at)
        end = hit.end() if hit else 0
    if not hit:
        return None
    lead = hit.group("lead").strip()
    first = lead.split()[0]
    if form.caps and (first != first.upper() or not any(c.isalpha() for c in first)):
        return None
    return Lead(lead, end)
