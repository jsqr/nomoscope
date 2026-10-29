"""Defined terms and their scopes.

A row whose quoted text does not contain its term is dropped; the model
either split one definition into its items or quoted the wrong text.
"""

import re

import polars as pl
from pydantic import BaseModel, Field

from nomoscope.extract import common, scopes
from nomoscope.extract.rows import Rows
from nomoscope.extract.task import Task

NAME = "definitions"
INDICATOR = re.compile(
    r"(?i)\b(?:means?|definitions?|shall mean|has the meaning|have the meanings|defined as)\b"
)
PROMPT_VERSION = "1"
PROMPT = """\
You read one passage of a code of law and list the terms it defines.

The first line names the jurisdiction. Then the passage may give heading
lines and a lead-in sentence, then a blank line, then the text. A
definition states what a term means for some part of the law: "X" means ...,
"X" includes ..., "X" has the meaning given in ..., "X" shall mean ..., or a
like form, with or without quotation marks around the term. List every
definition the passage states, in order. Leave out a term the passage only
uses as defined elsewhere ("as defined in Section 5") and a sentence that is
not a definition.

For each definition give:
- term: the defined term as written, without its quotation marks.
- scope: where the passage says the definition applies, as written: "this
  section", "this chapter", "this article", "this code", "Sections 100 to
  120". Null when the passage states none. A lead-in such as "As used in this
  chapter:" or "For purposes of this section," states the scope of every
  definition under it.
- text: the definition copied exactly from the passage, from the term through
  the end of the definition, including every listed item that belongs to it.
  Copy verbatim; do not paraphrase, shorten, or fix anything.
- confidence: a number from 0 to 1, how sure you are that this is a
  definition, that the term and text are as written, and that the scope is
  right. Give 1 only when nothing in the passage could read another way.

Also give governs: when the passage says that the definitions in one part of
the law govern a larger one ("the definitions in this article apply
throughout this chapter"), the larger one as written, "this chapter".
Otherwise null.
"""
QUOTE_MARKS = "\"'" + "".join(map(chr, (0x201C, 0x201D, 0x2018, 0x2019)))

SCHEMA = pl.Schema(
    {
        **common.COLUMNS,
        "term": pl.String,
        "term_key": pl.String,
        "scope_text": pl.String,
        "scope_kind": pl.String,
        "scope_id": pl.String,
        "scope_basis": pl.String,
    }
)


class Definition(BaseModel):
    term: str = Field(description="The defined term as written, without quotation marks.")
    scope: str | None = Field(
        description="Where the definition applies, as the passage states it, or null."
    )
    text: str = Field(description="The definition copied verbatim, term through end.")
    confidence: float = Field(
        description="0 to 1: how sure you are this is a definition, as written, with this scope."
    )


class Definitions(BaseModel):
    definitions: list[Definition]
    governs: str | None = Field(
        description="The larger division the passage's definitions govern, as written, or null."
    )


def term_key(term: str) -> str:
    """Without quotation marks, lowercased, spaces collapsed."""
    bare = term.translate(str.maketrans("", "", QUOTE_MARKS))
    return re.sub(r"\s+", " ", bare.strip()).lower()


def items(output: BaseModel) -> list[tuple[str, BaseModel]]:
    assert isinstance(output, Definitions)
    return [(d.text, d) for d in output.definitions]


def extra(output: BaseModel) -> dict[str, str] | None:
    assert isinstance(output, Definitions)
    return {"governs": output.governs} if output.governs else None


def build(rows: Rows, frame: pl.DataFrame) -> pl.DataFrame:
    idx = scopes.index(rows)
    governs = {b: e["governs"] for b, e in common.extras(frame).items() if e.get("governs")}
    governed = scopes.governing(idx, governs)
    where = common.placed(rows)
    numbering = common.Numbering("def")
    found = []
    for item in common.items(frame):
        d = Definition.model_validate_json(item.raw)
        if term_key(d.term) not in term_key(item.text):
            continue
        scope = scopes.scope_of(idx, item.block_id, item.start, d.scope, governed)
        found.append(
            {
                **common.base(rows, where, item, numbering.next(item.block_id)),
                "confidence": d.confidence,
                "term": d.term,
                "term_key": term_key(d.term),
                "scope_text": scope.text,
                "scope_kind": scope.kind,
                "scope_id": scope.id,
                "scope_basis": scope.basis,
            }
        )
    return common.frame(found, SCHEMA)


TASK = Task(
    name=NAME,
    indicator=INDICATOR,
    output=Definitions,
    prompt=PROMPT,
    prompt_version=PROMPT_VERSION,
    items=items,
    build=build,
    schema=SCHEMA,
    extra=extra,
)
