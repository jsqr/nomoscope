"""Extraction tasks and their settings tables."""

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import polars as pl
from pydantic import BaseModel

from nomoscope.chat import ChatModel, lookup
from nomoscope.extract import ExtractError
from nomoscope.extract.rows import Rows
from nomoscope.segments import CHARS_PER_TOKEN, UNITS, WORDS, Limit

TEXT = "text"
HISTORY = "history"
HEADING = "heading"
RUNNING = "running"
PUBLISHER = "publisher"
NOTES = (RUNNING, PUBLISHER)
SOURCES = (TEXT, HISTORY, HEADING, *NOTES)

Items = Callable[[BaseModel], list[tuple[str, BaseModel]]]
Extra = Callable[[BaseModel], Mapping[str, Any] | None]
Build = Callable[[Rows, pl.DataFrame], pl.DataFrame]

LIMIT = Limit(600, WORDS)
CONCURRENCY = 8


@dataclass(frozen=True)
class Task:
    # Also the settings table and the store partition.
    name: str
    indicator: re.Pattern[str]
    output: type[BaseModel]
    prompt: str
    prompt_version: str
    items: Items
    build: Build
    schema: pl.Schema
    sources: tuple[str, ...] = (TEXT,)
    extra: Extra = lambda output: None
    # A lead-in's citations belong to the lead-in's own unit, so a references
    # unit gets only its section and place as context.
    place_only: bool = False
    # Bump when the same stored items build a different dataset.
    version: str = "1"
    # When the task's name is not a usable SQL view name.
    dataset_name: str | None = None

    @property
    def dataset(self) -> str:
        return self.dataset_name or self.name

    @property
    def prompt_tokens(self) -> int:
        return -(-len(self.prompt) // int(CHARS_PER_TOKEN))

    def indicated(self, text: str | None) -> bool:
        return bool(text) and self.indicator.search(text or "") is not None


@dataclass(frozen=True)
class Settings:
    model: ChatModel
    limit: Limit = LIMIT
    concurrency: int = CONCURRENCY
    # Missing units tolerated when building, for ones an outage left behind.
    unextracted: int = 0


KEYS = ("model", "limit", "unit", "concurrency", "unextracted")


def settings(task: Task, table: Mapping[str, Any], models: Mapping[str, ChatModel]) -> Settings:
    unknown = sorted(set(table) - set(KEYS))
    if unknown:
        raise ExtractError(f"[{task.name}] {', '.join(unknown)}: unknown; known: {', '.join(KEYS)}")
    unit = str(table.get("unit", LIMIT.unit))
    if unit not in UNITS:
        raise ExtractError(f"[{task.name}] unit: one of {', '.join(UNITS)}, not {unit!r}")
    return Settings(
        lookup(models, str(table.get("model", "")), f"[{task.name}] model"),
        Limit(int(table.get("limit", LIMIT.size)), unit),
        int(table.get("concurrency", CONCURRENCY)),
        int(table.get("unextracted", 0)),
    )


def code_version(task: Task, s: Settings) -> str:
    return f"{task.version}.{s.model.slug}.{task.prompt_version}.{s.limit.tag}"
