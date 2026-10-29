"""Sending pending units to the chat model within a spend limit.

A unit is pending when the store has no row for its hash under the model and
prompt version. Units go in order until the estimated cost reaches the spend.
The store is written every FLUSH units, so an interrupted run keeps what it
paid for.

A reply that does not fit the output schema is sent back once with the
error. If it fails again, or the provider rejects the request, the unit is
stored as failed and stops being pending. Timeouts and throttling that
outlast the retries leave the unit pending for a later run.
"""

import json
import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx
import polars as pl
from pydantic import BaseModel, ValidationError

from nomoscope.chat import ChatError, ChatModel, Completion
from nomoscope.extract import locate, store
from nomoscope.extract.rows import Rows
from nomoscope.extract.store import HASH
from nomoscope.extract.task import Settings, Task
from nomoscope.extract.units import units
from nomoscope.providers import RETRY_STATUS, ProviderError
from nomoscope.settings import Settings as Config

log = logging.getLogger(__name__)

FLUSH = 100
PREFACE_TOKENS = 20
RETRY = "Your reply was rejected: {error}. Reply again with one JSON object that fits the schema."

Messages = list[dict[str, str]]
Complete = Callable[[Messages], Completion]


def preface(rows: Rows) -> str:
    if rows.name:
        return f"Jurisdiction: {rows.name} ({rows.jurisdiction})"
    return f"Jurisdiction: {rows.jurisdiction}"


def messages(task: Task, preface: str, context: str | None, body: str) -> Messages:
    content = f"{context}\n\n{body}" if context else body
    return [
        {"role": "system", "content": task.prompt},
        {"role": "user", "content": f"{preface}\n{content}"},
    ]


def cost(model: ChatModel, task: Task, est_tokens: int) -> float:
    tokens = est_tokens + task.prompt_tokens + PREFACE_TOKENS
    return model.dollars(tokens, int(tokens * model.output_per_input))


def pending(found: pl.DataFrame, done: set[str]) -> pl.DataFrame:
    return found.unique(HASH, keep="first", maintain_order=True).filter(
        ~pl.col(HASH).is_in(list(done))
    )


@dataclass(frozen=True)
class Answered:
    output: BaseModel
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class Failed:
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0


# None leaves the unit pending.
Outcome = Answered | Failed | None


def ask(task: Task, complete: Complete, sent: Messages) -> Outcome:
    tokens_in = tokens_out = 0
    error = ""
    for _ in range(2):
        try:
            reply = complete(sent)
        except ProviderError as e:
            if e.status is None or e.status in RETRY_STATUS:
                log.warning("left pending: %s", e)
                return None
            return Failed(f"provider {e.status}", tokens_in, tokens_out)
        except httpx.TransportError as e:
            log.warning("left pending: %s", e)
            return None
        except ChatError as e:
            return Failed(f"no usable output: {e}", tokens_in, tokens_out)
        tokens_in += reply.input_tokens
        tokens_out += reply.output_tokens
        try:
            return Answered(task.output.model_validate_json(reply.content), tokens_in, tokens_out)
        except ValidationError as e:
            error = str(e).splitlines()[0]
            sent = [
                *sent,
                {"role": "assistant", "content": reply.content},
                {"role": "user", "content": RETRY.format(error=error)},
            ]
    return Failed(f"output did not validate: {error}", tokens_in, tokens_out)


def stored_rows(
    task: Task, model: ChatModel, sha: str, context: str | None, body: str, outcome: Outcome
) -> list[tuple[Any, ...]]:
    if outcome is None:
        return []
    head = (sha, task.prompt_version)

    def tail(n: int, failed: str | None = None) -> tuple[Any, ...]:
        tokens = (outcome.input_tokens, outcome.output_tokens) if n == 0 else (0, 0)
        return (*tokens, model.slug, failed, 0)

    if isinstance(outcome, Failed):
        return [(*head, 0, None, None, None, None, *tail(0, outcome.reason))]
    items = task.items(outcome.output)
    spans = locate.located(context, body, [quote for quote, _ in items])
    kept = [(item, span) for (_, item), span in zip(items, spans, strict=True) if span]
    if len(kept) < len(items):
        log.debug("%s: %d items not found in the unit", sha[:12], len(items) - len(kept))
    extra = task.extra(outcome.output)
    extra_json = json.dumps(dict(extra), sort_keys=True) if extra else None
    if not kept:
        return [(*head, 0, None, None, None, extra_json, *tail(0))]
    return [
        (*head, n, item.model_dump_json(), start, end, extra_json if n == 0 else None, *tail(n))
        for n, (item, (start, end)) in enumerate(kept)
    ]


@dataclass(frozen=True)
class Result:
    jurisdiction: str
    task: str
    model: str
    pending: int
    dollars: float
    sent: int = 0
    items: int = 0
    failed: int = 0
    # Sent but left pending.
    left: int = 0
    spent: float = 0.0

    @property
    def summary(self) -> str:
        line = (
            f"{self.task:14} {self.jurisdiction:28} {self.pending:7} pending"
            f"  ${self.dollars:9.4f}  {self.model}"
        )
        if not self.sent:
            return line
        return (
            line + f"  sent {self.sent}: {self.items} items, {self.failed} failed,"
            f" {self.left} left pending, ${self.spent:.4f}"
        )


def affordable(costs: Sequence[float], spend: float) -> int:
    total = 0.0
    for i, c in enumerate(costs):
        total += c
        if total > spend:
            return i
    return len(costs)


def run(
    cfg: Config,
    task: Task,
    settings: Settings,
    rows: Rows,
    complete: Complete | None,
    *,
    spend: float = 0.0,
    writer: str = "unknown",
    flush: int = FLUSH,
) -> Result:
    """Without `complete` or a spend, only estimates."""
    model = settings.model
    j = rows.jurisdiction
    held = store.held(cfg, task.name, model, j, task.prompt_version)
    todo = pending(units(task, rows, settings.limit), set(held[HASH].to_list()))
    costs = [cost(model, task, n) for n in todo["est_tokens"].to_list()]
    estimate = Result(j, task.name, model.slug, todo.height, sum(costs))
    if complete is None or spend <= 0 or todo.is_empty():
        return estimate
    todo = todo.head(affordable(costs, spend))
    line = preface(rows)
    sent = items = failed = left = 0
    spent = 0.0
    for start in range(0, todo.height, flush):
        chunk = todo.slice(start, flush)
        columns = chunk.select(HASH, "context", "body").rows()
        with ThreadPoolExecutor(max_workers=settings.concurrency) as pool:
            outcomes = list(
                pool.map(lambda r: ask(task, complete, messages(task, line, r[1], r[2])), columns)
            )
        found = [
            r
            for (sha, context, body), o in zip(columns, outcomes, strict=True)
            for r in stored_rows(task, model, sha, context, body, o)
        ]
        if found:
            frame = pl.DataFrame(found, schema=store.SCHEMA, orient="row")
            store.write(cfg, task.name, model, j, frame, writer)
            items += frame.filter(pl.col("item").is_not_null()).height
        for o in outcomes:
            if o is None:
                left += 1
                continue
            failed += isinstance(o, Failed)
            spent += model.dollars(o.input_tokens, o.output_tokens)
        sent += len(outcomes)
        log.info("%s %s: %d/%d units sent", j, task.name, sent, todo.height)
    return Result(
        j, task.name, model.slug, estimate.pending, estimate.dollars,
        sent, items, failed, left, spent,
    )  # fmt: skip
