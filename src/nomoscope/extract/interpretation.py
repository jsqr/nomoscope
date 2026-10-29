"""Rules of interpretation and their scopes.

Examples: the singular includes the plural; "shall" is mandatory; how days
are counted; headings are not law; references include amendments; which
provision prevails; severability. Definitions of terms are excluded.
"""

import re
from typing import Literal

import polars as pl
from pydantic import BaseModel, Field

from nomoscope.extract import common, scopes
from nomoscope.extract.rows import Rows
from nomoscope.extract.task import Task

NAME = "interpretation"
INDICATOR = re.compile(
    r"(?i)\b(?:constru(?:ed|ction|e)|interpret(?:ed|ation)?|singular|plural|gender"
    r"|severab(?:le|ility)|invalid|headings?|computed|computation|mandatory|permissive"
    r"|conflicts?|inconsistent|prevail|amended|tense|liberally)\b"
)
Kind = Literal[
    "number_and_gender",
    "tense",
    "mandatory_and_permissive",
    "computation_of_time",
    "headings",
    "amended_references",
    "conflicts",
    "severability",
    "liberal_construction",
    "other",
]
PROMPT_VERSION = "1"
PROMPT = """\
You read one passage of a code of law and list the rules it gives for
reading the law.

The first line names the jurisdiction. Then the passage may give heading
lines and a lead-in sentence, then a blank line, then the text. A rule of
interpretation tells a reader how other provisions are to be read, not what
a particular term means. List every such rule the passage states, in order.
Leave out definitions of particular terms ("'Building' means ...") and
provisions that regulate conduct.

For each rule give:
- kind: one of number_and_gender (the singular includes the plural, one
  gender includes the others), tense (the present includes the future),
  mandatory_and_permissive ("shall" is mandatory, "may" is permissive),
  computation_of_time (how days or periods are counted), headings (headings
  and captions are not part of the law), amended_references (a reference to
  a law includes its amendments), conflicts (which provision prevails when
  two conflict), severability (an invalid provision leaves the rest in
  force), liberal_construction (the law is to be construed liberally or
  strictly), or other.
- scope: where the passage says the rule applies, as written: "this
  chapter", "this code", "this section". Null when the passage states none.
- text: the rule copied exactly from the passage, the whole sentence or
  item. Copy verbatim; do not paraphrase, shorten, or fix anything.
- confidence: a number from 0 to 1, how sure you are that this is a rule of
  interpretation, of this kind, as written, with this scope.
"""

SCHEMA = pl.Schema(
    {
        **common.COLUMNS,
        "kind": pl.String,
        "scope_text": pl.String,
        "scope_kind": pl.String,
        "scope_id": pl.String,
        "scope_basis": pl.String,
    }
)


class Rule(BaseModel):
    kind: Kind
    scope: str | None = Field(description="Where the rule applies, as written, or null.")
    text: str = Field(description="The rule copied verbatim from the passage.")
    confidence: float = Field(description="0 to 1: how sure you are of the rule and its scope.")


class Rules(BaseModel):
    rules: list[Rule]


def items(output: BaseModel) -> list[tuple[str, BaseModel]]:
    assert isinstance(output, Rules)
    return [(r.text, r) for r in output.rules]


def build(rows: Rows, frame: pl.DataFrame) -> pl.DataFrame:
    idx = scopes.index(rows)
    where = common.placed(rows)
    numbering = common.Numbering("rule")
    found = []
    for item in common.items(frame):
        r = Rule.model_validate_json(item.raw)
        scope = scopes.scope_of(idx, item.block_id, item.start, r.scope, {})
        found.append(
            {
                **common.base(rows, where, item, numbering.next(item.block_id)),
                "confidence": r.confidence,
                "kind": r.kind,
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
    output=Rules,
    prompt=PROMPT,
    prompt_version=PROMPT_VERSION,
    items=items,
    build=build,
    schema=SCHEMA,
)
