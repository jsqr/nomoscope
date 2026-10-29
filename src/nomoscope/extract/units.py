"""Extraction units. Each unit is one request to the model.

A version that fits the task's limit is one unit. A longer one is cut at its
subdivisions as segments are (`derive/segment.py`), and each piece holding an
indicator word is a unit, with the heading path and enclosing lead-ins as
context. A lead-in is in no piece's span, so a lead-in holding an indicator
word is a unit of its own; otherwise every piece under it would report it.

History notes, headings, and notes are whole units with a one-line label as
context.

Units are hashed like segments, context and text together, so repeated text
is sent once.
"""

from typing import Any

import polars as pl

from nomoscope.derive import datasets, segment
from nomoscope.extract import task as t
from nomoscope.extract.rows import Rows
from nomoscope.extract.task import Task
from nomoscope.segments import CHARS_PER_TOKEN, Limit

SCHEMA = pl.Schema(
    {
        "source": pl.String,
        # A version, history wording, heading, or note.
        "block_id": pl.String,
        "ordinal": pl.Int64,
        "start_char": pl.Int64,
        "end_char": pl.Int64,
        "context": pl.String,
        "body": pl.String,
        "content_sha256": pl.String,
        "est_tokens": pl.Int64,
    }
)
LABELS = {
    t.HISTORY: "History note",
    t.HEADING: "Heading",
    t.RUNNING: "Running header or footer",
    t.PUBLISHER: "Publisher's note",
}


def units(task: Task, rows: Rows, limit: Limit) -> pl.DataFrame:
    """Repeated units included."""
    frames = []
    if t.TEXT in task.sources:
        frames.append(section_units(task, rows, limit))
    if t.HISTORY in task.sources:
        frames.append(whole_units(task, t.HISTORY, rows.history.select("id", "history")))
    if t.HEADING in task.sources:
        heading = datasets.spaced(pl.col("number"), pl.col("heading"))
        frames.append(whole_units(task, t.HEADING, rows.nodes.select("id", heading)))
    for kind in t.NOTES:
        if kind in task.sources and rows.notes is not None:
            notes = rows.notes.filter(pl.col("kind") == kind).select("id", "text")
            frames.append(whole_units(task, kind, notes))
    return pl.concat(frames) if frames else pl.DataFrame(schema=SCHEMA)


def section_units(task: Task, rows: Rows, limit: Limit) -> pl.DataFrame:
    blocks = datasets.section_blocks(rows.versions, rows.nodes)
    parsed = datasets.parsed_versions(rows.versions)
    found = []
    for block_id, headings, text, indents in blocks.select(
        "block_id", "context", "text", "indents"
    ).iter_rows():
        text = text or ""
        if not task.indicated(text):
            continue
        spans: list[tuple[int, int, str | None]] = []
        leads: set[str] = set()
        for p in segment.cut(text, limit, indents, parsed.get(block_id)):
            for lead in p.chapeau:
                if lead in leads:
                    continue
                leads.add(lead)
                span = lead_span(text, lead)
                if span and task.indicated(lead):
                    spans.append((*span, headings))
            if task.indicated(text[p.start : p.end]):
                context = place(headings, p) if task.place_only else segment.context(headings, p)
                spans.append((p.start, p.end, context))
        for ordinal, (start, end, context) in enumerate(sorted(spans)):
            found.append(row(t.TEXT, block_id, ordinal, start, end, context, text[start:end]))
    return pl.DataFrame(found, schema=SCHEMA, orient="row")


def whole_units(task: Task, source: str, frame: pl.DataFrame) -> pl.DataFrame:
    found = [
        row(source, block_id, 0, 0, len(text), LABELS[source], text)
        for block_id, text in frame.iter_rows()
        if text and task.indicated(text)
    ]
    return pl.DataFrame(found, schema=SCHEMA, orient="row")


def row(
    source: str, block_id: str, ordinal: int, start: int, end: int, context: str | None, body: str
) -> tuple[Any, ...]:
    return (
        source,
        block_id,
        ordinal,
        start,
        end,
        context,
        body,
        segment.content_sha256(context, body),
        estimated_tokens(context, body),
    )


def place(headings: str | None, p: segment.Segment) -> str | None:
    section = headings.split(datasets.CONTEXT_SEP)[-1] if headings else ""
    return f"{section} {p.label}".strip() or None


def lead_span(text: str, lead: str) -> tuple[int, int] | None:
    """The cut shortens a long lead-in to its enumerators and last sentence, so
    the last sentence alone is tried after the whole."""
    for needle in (lead, lead.split(") ")[-1]):
        at = text.find(needle)
        if at >= 0:
            return at, at + len(needle)
    return None


def estimated_tokens(context: str | None, body: str) -> int:
    return -(-len(segment.content(context, body)) // int(CHARS_PER_TOKEN))
