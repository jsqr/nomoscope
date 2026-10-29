"""Penalties and other consequences of violations, with the part of the law
each applies to.

A general penalty, one that applies where no other is provided, has scope
basis `default`.
"""

import re
from typing import Literal

import polars as pl
from pydantic import BaseModel, Field

from nomoscope.extract import common, scopes
from nomoscope.extract.rows import Rows
from nomoscope.extract.task import Task

NAME = "penalties"
INDICATOR = re.compile(
    r"(?i)\b(?:penalt(?:y|ies)|fined?|fines|imprison(?:ed|ment)?|jail|misdemeanor|felony"
    r"|violations?|offen[cs]es?|punish(?:ed|able|ment)?|guilty|revo(?:ke|cation)"
    r"|forfeit(?:ure)?|abate(?:ment)?|infraction)\b"
)
Character = Literal["civil", "criminal", "unstated"]
Offense = Literal["first", "second", "subsequent", "any"]
FinePer = Literal["offense", "day", "unstated"]
Other = Literal[
    "revocation", "suspension", "abatement", "cost_recovery", "injunction", "lien", "other"
]
PROMPT_VERSION = "1"
PROMPT = """\
You read one passage of a code of law and list the penalties and other
consequences it attaches to violations.

The first line names the jurisdiction. Then the passage may give heading
lines and a lead-in sentence, then a blank line, then the text. List each
consequence the passage states, in order: a fine, a term of imprisonment, a
civil penalty, or another consequence such as revocation or suspension of a
license or permit, abatement of a nuisance at the violator's cost, recovery
of costs, an injunction, or a lien. A schedule of fines that rise with each
offense is one item per offense level. Leave out fees that are not
penalties, such as permit fees.

For each consequence give:
- conduct: what is penalized, as written, short: "violating any provision of
  this chapter", "failure to obtain a permit".
- scope: the part of the law whose violation is penalized, as written: "this
  chapter", "this section", "this code". Null when the passage states none.
- general: true when the passage says it applies to violations for which no
  other penalty is provided.
- classification: what the passage calls the offense, as written:
  "violation", "misdemeanor", "Class B misdemeanor", "infraction"; null when
  it calls it nothing.
- character: civil or criminal, when the passage says or the classification
  makes plain; otherwise unstated.
- fine_min and fine_max: the least and greatest fine in dollars, as numbers;
  null when none is stated. fine_per: offense, day, or unstated.
- imprisonment_max_days: the longest term of imprisonment in days (15 days
  is 15, six months is 180, one year is 365); null when none.
- offense: first, second, subsequent, or any: the offenses this item applies
  to.
- each_day_separate: true when the passage says each day a violation
  continues is a separate offense.
- other: the other consequences this item states, from revocation,
  suspension, abatement, cost_recovery, injunction, lien, other; empty when
  none.
- text: the provision copied exactly from the passage, the sentence or item
  that states this consequence. Copy verbatim; do not paraphrase, shorten,
  or fix anything.
- confidence: a number from 0 to 1, how sure you are of this item as a
  whole.
"""

SCHEMA = pl.Schema(
    {
        **common.COLUMNS,
        "conduct": pl.String,
        "classification": pl.String,
        "character": pl.String,
        "fine_min": pl.Float64,
        "fine_max": pl.Float64,
        "fine_per": pl.String,
        "imprisonment_max_days": pl.Int64,
        "offense": pl.String,
        "each_day_separate": pl.Boolean,
        "other": pl.List(pl.String),
        "scope_text": pl.String,
        "scope_kind": pl.String,
        "scope_id": pl.String,
        "scope_basis": pl.String,
    }
)


class Penalty(BaseModel):
    conduct: str | None = Field(description="What is penalized, as written, short.")
    scope: str | None = Field(description="The part of the law whose violation it penalizes.")
    general: bool = Field(description="Whether it applies where no other penalty is provided.")
    classification: str | None = Field(description="What the passage calls the offense.")
    character: Character
    fine_min: float | None
    fine_max: float | None
    fine_per: FinePer
    imprisonment_max_days: int | None
    offense: Offense
    each_day_separate: bool
    other: list[Other]
    text: str = Field(description="The provision copied verbatim from the passage.")
    confidence: float = Field(description="0 to 1: how sure you are of this item.")


class Penalties(BaseModel):
    penalties: list[Penalty]


def items(output: BaseModel) -> list[tuple[str, BaseModel]]:
    assert isinstance(output, Penalties)
    return [(p.text, p) for p in output.penalties]


def scope_of(idx: scopes.Index, block_id: str, at: int, p: Penalty) -> scopes.Scope:
    """A general penalty with no stated scope covers its code."""
    basis = scopes.DEFAULT if p.general else scopes.STATED
    if p.scope is not None or not p.general:
        return scopes.resolve(idx, block_id, at, p.scope, basis)
    code = idx.code_of(block_id)
    return scopes.Scope(None, scopes.CODE, scopes.code_id(idx, code) if code else None, basis)


def build(rows: Rows, frame: pl.DataFrame) -> pl.DataFrame:
    idx = scopes.index(rows)
    where = common.placed(rows)
    numbering = common.Numbering("penalty")
    found = []
    for item in common.items(frame):
        p = Penalty.model_validate_json(item.raw)
        scope = scope_of(idx, item.block_id, item.start, p)
        found.append(
            {
                **common.base(rows, where, item, numbering.next(item.block_id)),
                "confidence": p.confidence,
                "conduct": p.conduct,
                "classification": p.classification,
                "character": p.character,
                "fine_min": p.fine_min,
                "fine_max": p.fine_max,
                "fine_per": p.fine_per,
                "imprisonment_max_days": p.imprisonment_max_days,
                "offense": p.offense,
                "each_day_separate": p.each_day_separate,
                "other": list(p.other),
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
    output=Penalties,
    prompt=PROMPT,
    prompt_version=PROMPT_VERSION,
    items=items,
    build=build,
    schema=SCHEMA,
)
