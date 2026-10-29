"""Columns and helpers common to the extraction datasets.

`start_char` and `end_char` index the block's text: a version's text, a
history note, a heading's number and title, or a note.
"""

import json
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Any

import polars as pl

from nomoscope.derive import ids
from nomoscope.extract.rows import Rows

COLUMNS = {
    "id": pl.String,
    "jurisdiction": pl.String,
    "source": pl.String,
    "block_id": pl.String,
    "section_id": pl.String,
    "node_id": pl.String,
    "start_char": pl.Int64,
    "end_char": pl.Int64,
    "text": pl.String,
    "confidence": pl.Float64,
    "content_sha256": pl.String,
    "model": pl.String,
    "prompt_version": pl.String,
}


@dataclass(frozen=True)
class Item:
    source: str
    block_id: str
    start: int
    end: int
    text: str
    raw: str
    content_sha256: str
    prompt_version: str
    extra: Mapping[str, Any] | None


def items(frame: pl.DataFrame) -> Iterator[Item]:
    columns = (
        "source", "block_id", "start_char", "body", "item", "item_start", "item_end",
        "content_sha256", "prompt_version", "extra",
    )  # fmt: skip
    for source, block_id, at, body, raw, start, end, sha, version, extra in frame.select(
        columns
    ).iter_rows():
        if raw is None:
            continue
        yield Item(
            source, block_id, at + start, at + end, body[start:end], raw, sha, version,
            json.loads(extra) if extra else None,
        )  # fmt: skip


def extras(frame: pl.DataFrame) -> dict[str, dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for block_id, extra in (
        frame.filter(pl.col("extra").is_not_null()).select("block_id", "extra").iter_rows()
    ):
        found.setdefault(block_id, {}).update(json.loads(extra))
    return found


def placed(rows: Rows) -> dict[str, tuple[str | None, str | None]]:
    """Block id -> (section id, heading id). A history note belongs to its
    version's section; a heading to itself."""
    found: dict[str, tuple[str | None, str | None]] = {}
    for version, section, node in rows.versions.select("id", "section_id", "node_id").iter_rows():
        found[version] = (section, node)
    for history, version in rows.history.select("id", "law_version_id").iter_rows():
        found[history] = found.get(version, (None, None))
    for node in rows.nodes["id"].to_list():
        found[node] = (None, node)
    if rows.notes is not None:
        for note, target, section in rows.notes.select(
            "id", "target_id", "section_num"
        ).iter_rows():
            found[note] = (target, None) if section is not None else (None, target)
    return found


class Numbering:
    """<block>/<kind>/<n>"""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        self.counts: Counter[str] = Counter()

    def next(self, block_id: str) -> str:
        n = self.counts[block_id]
        self.counts[block_id] += 1
        return ids.compose(block_id, self.kind, str(n))


def base(
    rows: Rows, where: Mapping[str, tuple[str | None, str | None]], item: Item, item_id: str
) -> dict[str, Any]:
    section, node = where.get(item.block_id, (None, None))
    return {
        "id": item_id,
        "jurisdiction": rows.jurisdiction,
        "source": item.source,
        "block_id": item.block_id,
        "section_id": section,
        "node_id": node,
        "start_char": item.start,
        "end_char": item.end,
        "text": item.text,
        "content_sha256": item.content_sha256,
        "prompt_version": item.prompt_version,
    }


def frame(found: list[dict[str, Any]], schema: pl.Schema) -> pl.DataFrame:
    if not found:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(found, schema={k: v for k, v in schema.items() if k != "model"})
