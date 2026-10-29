"""Enacting instruments named in a code, with their actions and dates.

Units come from section text, history notes, headings, running headers and
footers, and publisher's notes. One row per instrument and action. The id
matches the one `citations` gives a reference to the instrument, and is null
when the name tables do not know the kind. The dates are not combined with
a version's other dates.
"""

import re
from datetime import date
from typing import Literal

import polars as pl
from pydantic import BaseModel, Field

from nomoscope.extract import citations, common, scopes
from nomoscope.extract import task as t
from nomoscope.extract.citations import Cited
from nomoscope.extract.rows import Rows
from nomoscope.extract.task import Task

NAME = "enactments"
INDICATOR = re.compile(
    r"(?i)\b(?:ord\.|res\.|eff\.|ch\.|l\.\s?l\.|(?:ordinances?|local laws?|resolutions?"
    r"|adopted|amended|added|repealed|renumbered|enacted|effective|passed|approved"
    r"|laws of)\b)"
)
Action = Literal[
    "adopted", "added", "amended", "repealed", "renumbered", "readopted", "effective", "other"
]
PROMPT_VERSION = "1"
PROMPT = """\
You read one piece of a code of law and list the enacting instruments it
names: the local laws, ordinances, resolutions, bills, or session laws that
adopted, amended, or repealed text of the code.

The first line names the jurisdiction. The next line says what the piece is:
a history note, a heading, a running header or footer, a publisher's note,
or the heading path above a section's text. Then a blank line, then the
text. List each instrument and what the piece says it did, in order. An
instrument named twice for two actions is two items. Leave out a citation
to law that does not say it enacted or changed this text ("as required by
Section 5 of the Town Law").

For each item give:
- instrument: the instrument as written ("L.L. No. 1-1991", "Ord. 2005-3",
  "Local Law No. 2 of 2010", "Res. 12-2004").
- enactment: the word for its kind as written ("L.L.", "Local Law", "Ord.",
  "Ordinance", "Res."); number: its number as written ("1", "2005-3");
  year: its year as a number, from the number or a date when the text
  gives no other; null when not written.
- action: adopted, added, amended, repealed, renumbered, readopted,
  effective, or other.
- adopted_date, effective_date, filed_date: the dates the text gives, as
  YYYY-MM-DD; null when it gives none. A date written only as a year is
  null.
- subdivision: the part of the section the action applied to, as written
  ("Subsection B", "(c)"); null when the text names none.
- text: the words that name the instrument and its action, copied exactly
  from the piece. Copy verbatim; do not paraphrase or fix anything.
- confidence: a number from 0 to 1, how sure you are of this item.
"""

SCHEMA = pl.Schema(
    {
        **common.COLUMNS,
        "instrument": pl.String,
        "enactment": pl.String,
        "number": pl.String,
        "year": pl.Int64,
        "enactment_id": pl.String,
        "action": pl.String,
        "adopted_date": pl.Date,
        "effective_date": pl.Date,
        "filed_date": pl.Date,
        "subdivision": pl.String,
    }
)


class Enactment(BaseModel):
    instrument: str = Field(description="The instrument as written.")
    enactment: str | None = Field(description="The word for its kind as written.")
    number: str | None
    year: int | None
    action: Action
    adopted_date: str | None = Field(description="YYYY-MM-DD, or null.")
    effective_date: str | None = Field(description="YYYY-MM-DD, or null.")
    filed_date: str | None = Field(description="YYYY-MM-DD, or null.")
    subdivision: str | None
    text: str = Field(description="The words that name it and its action, verbatim.")
    confidence: float = Field(description="0 to 1: how sure you are of this item.")


class Enactments(BaseModel):
    enactments: list[Enactment]


def items(output: BaseModel) -> list[tuple[str, BaseModel]]:
    assert isinstance(output, Enactments)
    return [(e.text, e) for e in output.enactments]


def parsed(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def build(rows: Rows, frame: pl.DataFrame) -> pl.DataFrame:
    idx = scopes.index(rows)
    where = common.placed(rows)
    numbering = common.Numbering("enactment")
    found = []
    for item in common.items(frame):
        e = Enactment.model_validate_json(item.raw)
        c = Cited(
            e.instrument, citations.ENACTMENT, None, None, (), (), None,
            e.enactment, e.number, e.year, False,
        )  # fmt: skip
        target = citations.enactment_id(idx, rows.names, c)
        found.append(
            {
                **common.base(rows, where, item, numbering.next(item.block_id)),
                "confidence": e.confidence,
                "instrument": e.instrument,
                "enactment": e.enactment,
                "number": e.number,
                "year": e.year,
                "enactment_id": target.id,
                "action": e.action,
                "adopted_date": parsed(e.adopted_date),
                "effective_date": parsed(e.effective_date),
                "filed_date": parsed(e.filed_date),
                "subdivision": e.subdivision,
            }
        )
    return common.frame(found, SCHEMA)


TASK = Task(
    name=NAME,
    indicator=INDICATOR,
    output=Enactments,
    prompt=PROMPT,
    prompt_version=PROMPT_VERSION,
    items=items,
    build=build,
    schema=SCHEMA,
    sources=t.SOURCES,
)
