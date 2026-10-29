"""Citations of law and their resolved targets.

The model gives each citation's parts as written and a Bluebook form;
`citations` resolves them. A contiguous range is one row; each item of a
list is its own row.
"""

import re
from typing import Literal

import polars as pl
from pydantic import BaseModel, Field

from nomoscope.extract import citations, common, scopes
from nomoscope.extract.citations import Cited
from nomoscope.extract.rows import Rows
from nomoscope.extract.task import Task

NAME = "references"
INDICATOR = re.compile(
    r"(?i)\b(?:sections?|subsections?|subdivisions?|paragraphs?|subparagraphs?|clauses?"
    r"|chapters?|articles?|parts?|divisions?|titles?|codes?|acts?|laws?|statutes?"
    r"|ordinances?|resolutions?|constitution|regulations?|rules)\b|\u00a7|\bU\.\s?S\.\s?C\b"
    r"|\bC\.\s?F\.\s?R\b|\bN\.?Y\.?C\.?R\.?R\b"
)
Kind = Literal[
    "section",
    "subdivision",
    "division",
    "code",
    "enactment",
    "constitution",
    "regulation",
    "federal",
    "model_code",
    "other",
]
PROMPT_VERSION = "1"
PROMPT = """\
You read one passage of a code of law and list every reference it makes to
law.

The passage opens with a line naming the jurisdiction, then a line naming
the code, the section, and the passage's own place in it, then a blank
line, then the text.

A reference is a citation to a section, a subdivision of one, a division of
a code (title, chapter, article, part), a whole code or act, an enacting
instrument (a local law, an ordinance, a resolution, a bill, a chapter of
session laws), a constitution, a regulation, federal law, or a model code
(such as the International Building Code). List every reference in the
passage, in order. Each contiguous range ("Sections 100 to 120, inclusive",
"subsections (a) through (c)") is one reference; each item of a list
("Sections 100, 105, and 110") is its own reference, and so is each of
"subsections (a) and (c)". A reference to the passage's own section, code,
or an enclosing division ("this section", "subsection (a)", "this chapter")
is a reference too.

A passage that is a table or an index rather than operative text -- a list
of fees, a table of amendments, a bare list of section numbers under a
heading -- states no references. Return an empty list.

For each reference give:
- text: the reference copied exactly from the passage, the whole citation
  phrase. Copy verbatim; do not paraphrase or shorten.
- kind: section, subdivision, division, code, enactment, constitution,
  regulation, federal, model_code, or other.
- code: the code, act, or body of rules the reference names, as written
  ("Town Law", "Vehicle and Traffic Law", "this Code", "Uniform Fire
  Prevention and Building Code"); null when it names none.
- section: the section number as written, without the word "Section" or the
  section sign; null when none.
- path: the subdivision enumerators, outermost first, without parentheses
  (["b", "1"] for "paragraph (1) of subsection (b)"); empty when none.
- section_end and path_end: the end of a contiguous range, in the same
  form; null and empty when the reference is a single unit.
- levels: the divisions named, outermost first, each with its level word in
  lowercase ("title", "chapter", "article", "part") and its number as
  written; empty when none. commencing_section: the section number after
  "commencing with Section", else null.
- enactment: for an enacting instrument, the word for its kind as written
  ("Local Law", "L.L.", "Ordinance", "Ord.", "Resolution", "Laws of");
  enactment_number its number as written ("2", "2005-3"); enactment_year
  its year as a number; null otherwise.
- relative: true when the reference leans on the passage's own place --
  "this section", "this chapter", "subsection (a)" with no section named,
  "paragraph (2) of this subsection". Give the parts exactly as written: for
  a division, the level word with a null number; for a subdivision, the
  enumerators. Do not work out what it points to, and do not fill in the
  passage's own section or code.
- bluebook: the reference in Bluebook form for the jurisdiction named in the
  first line ("N.Y. Town Law \u00a7 130", "N.Y. Comp. Codes R. & Regs. tit. 19, \u00a7
  1203.3", "42 U.S.C. \u00a7 1983"). Null for a relative reference, and null when
  no Bluebook form applies.
- confidence: a number from 0 to 1, how sure you are that this is a
  reference and that its parts are as written.
"""

SCHEMA = pl.Schema(
    {
        **common.COLUMNS,
        "kind": pl.String,
        "code": pl.String,
        "section": pl.String,
        "path": pl.String,
        "section_end": pl.String,
        "path_end": pl.String,
        "enactment": pl.String,
        "enactment_number": pl.String,
        "enactment_year": pl.Int64,
        "relative": pl.Boolean,
        "bluebook": pl.String,
        "target_kind": pl.String,
        "target_id": pl.String,
        "target_status": pl.String,
        "target_end_id": pl.String,
    }
)


class Level(BaseModel):
    level: str = Field(description="The level word, lowercase: title, chapter, article, ...")
    number: str | None = Field(description="The number as written, or null.")


class Reference(BaseModel):
    text: str = Field(description="The citation phrase copied verbatim from the passage.")
    kind: Kind
    code: str | None
    section: str | None
    path: list[str]
    section_end: str | None
    path_end: list[str]
    levels: list[Level]
    commencing_section: str | None
    enactment: str | None
    enactment_number: str | None
    enactment_year: int | None
    relative: bool
    bluebook: str | None
    confidence: float = Field(description="0 to 1: how sure you are of this reference.")


class References(BaseModel):
    references: list[Reference]


def items(output: BaseModel) -> list[tuple[str, BaseModel]]:
    assert isinstance(output, References)
    return [(r.text, r) for r in output.references]


def cited(r: Reference) -> Cited:
    return Cited(
        r.text, r.kind, r.code, r.section, r.path,
        [(v.level, v.number) for v in r.levels], r.commencing_section,
        r.enactment, r.enactment_number, r.enactment_year, r.relative,
    )  # fmt: skip


def build(rows: Rows, frame: pl.DataFrame) -> pl.DataFrame:
    idx = scopes.index(rows)
    where = common.placed(rows)
    numbering = common.Numbering("ref")
    found = []
    for item in common.items(frame):
        r = Reference.model_validate_json(item.raw)
        start, end = citations.resolve(
            idx, rows.names, item.block_id, item.start, cited(r), r.section_end, r.path_end
        )
        found.append(
            {
                **common.base(rows, where, item, numbering.next(item.block_id)),
                "confidence": r.confidence,
                "kind": r.kind,
                "code": r.code,
                "section": r.section,
                "path": "/".join(r.path) or None,
                "section_end": r.section_end,
                "path_end": "/".join(r.path_end) or None,
                "enactment": r.enactment,
                "enactment_number": r.enactment_number,
                "enactment_year": r.enactment_year,
                "relative": r.relative,
                "bluebook": r.bluebook,
                "target_kind": start.kind,
                "target_id": start.id,
                "target_status": start.status,
                "target_end_id": end.id,
            }
        )
    return common.frame(found, SCHEMA)


TASK = Task(
    name=NAME,
    indicator=INDICATOR,
    output=References,
    prompt=PROMPT,
    prompt_version=PROMPT_VERSION,
    items=items,
    build=build,
    schema=SCHEMA,
    place_only=True,
    version="2",
    # REFERENCES is reserved in SQL.
    dataset_name="cross_references",
)
