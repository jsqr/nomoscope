"""Build step for the extraction datasets, one per task and jurisdiction.

A task runs only if its settings table names a model. Inputs: the
jurisdiction's derive step, the store's files, and the citation name tables.
The step builds derived/<task>/jurisdiction=<j>/data.parquet only when no
more than `unextracted` units are missing from the store; otherwise it
records nothing and reports the count.
"""

import hashlib
import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

import duckdb
import polars as pl

from nomoscope.chat import ChatModel
from nomoscope.extract import names as names_mod
from nomoscope.extract import rows as rows_mod
from nomoscope.extract import store, tasks
from nomoscope.extract import task as task_mod
from nomoscope.extract.names import Names
from nomoscope.extract.store import HASH
from nomoscope.extract.task import Settings as TaskSettings
from nomoscope.extract.task import Task
from nomoscope.extract.units import units
from nomoscope.graph import fingerprint, steps
from nomoscope.graph.steps import CONFIG, DERIVE, EXTRACT, EXTRACTIONS, Input, Planned, StepKey
from nomoscope.graph.store import Store
from nomoscope.parquet import write_parquet
from nomoscope.partition import dataset_path
from nomoscope.settings import Settings

log = logging.getLogger(__name__)

ITEM_COLUMNS = {"ordinal": "item_ordinal", "start_char": "item_start", "end_char": "item_end"}


@dataclass(frozen=True)
class Configured:
    task: Task
    settings: TaskSettings


def configured(cfg: Settings, models: Mapping[str, ChatModel]) -> list[Configured]:
    return [
        Configured(t, task_mod.settings(t, cfg.table(t.name), models))
        for t in tasks.TASKS
        if "model" in cfg.table(t.name)
    ]


def code_version(c: Configured) -> str:
    return fingerprint.code_version(task_mod.code_version(c.task, c.settings), c.task.schema)


def store_input(cfg: Settings, c: Configured, jurisdiction: str) -> Input:
    found = store.files(cfg, c.task.name, c.settings.model, jurisdiction)
    digest = hashlib.sha256("\n".join(p.name for p in found).encode()).hexdigest()
    return Input(EXTRACTIONS, c.task.name, jurisdiction, digest)


def derived(conn: duckdb.DuckDBPyConnection) -> dict[str, str]:
    rows = conn.execute("SELECT scope, fingerprint FROM steps WHERE stage = ?", [DERIVE]).fetchall()
    return dict(rows)


def plan(
    cfg: Settings,
    conn: duckdb.DuckDBPyConnection,
    found: Sequence[Configured],
    names: Names,
    jurisdictions: Iterable[str] | None = None,
) -> list[Planned]:
    wanted = set(jurisdictions) if jurisdictions is not None else None
    built = derived(conn)
    config = Input(CONFIG, names_mod.SCOPE, "", names.fingerprint)
    plans = []
    for c in found:
        version = code_version(c)
        for j in sorted(built):
            if wanted is not None and j not in wanted:
                continue
            inputs = (Input(DERIVE, j, "", built[j]), store_input(cfg, c, j), config)
            plans.append(Planned(StepKey(EXTRACT, c.task.name, j), inputs, version))
    return plans


@dataclass(frozen=True)
class Built:
    task: str
    jurisdiction: str
    rows: int | None
    pending: int = 0

    @property
    def summary(self) -> str:
        if self.rows is None:
            return f"{self.task:14} {self.jurisdiction:24} {self.pending} units pending; not built"
        return f"{self.task:14} {self.jurisdiction:24} {self.rows:9} rows"


def joined(cfg: Settings, c: Configured, rows: rows_mod.Rows) -> tuple[pl.DataFrame, int]:
    """Stored items joined to their units, and the count of missing units."""
    found = units(c.task, rows, c.settings.limit)
    held = store.held(cfg, c.task.name, c.settings.model, rows.jurisdiction, c.task.prompt_version)
    missing = found.filter(~pl.col(HASH).is_in(held[HASH].implode()))[HASH].n_unique()
    items = found.join(held.rename(ITEM_COLUMNS), on=HASH, how="inner").sort(
        "source", "block_id", "start_char", "item_ordinal"
    )
    return items, missing


def build_one(
    cfg: Settings, s: Store, c: Configured, planned: Planned, names: Names
) -> Built | None:
    j = planned.key.unit
    rows = rows_mod.load(cfg, j)
    if rows is None:
        return None
    items, missing = joined(cfg, c, rows)
    if missing > c.settings.unextracted:
        return Built(c.task.name, j, None, missing)
    frame = (
        c.task.build(replace(rows, names=names), items)
        .with_columns(pl.lit(c.settings.model.slug).alias("model"))
        .select(c.task.schema.names())
        .cast(c.task.schema)
    )
    path = dataset_path(cfg, c.task.name, j)
    write_parquet(frame, path)
    sha256 = fingerprint.file_sha256(path)
    table = steps.table_row(cfg, c.task.name, path, frame.schema, frame.height, sha256=sha256)
    with s.open() as conn:
        steps.record(conn, planned, [table], {"pending": missing})
    log.info("%s %s: %d rows", c.task.name, j, frame.height)
    return Built(c.task.name, j, frame.height, missing)


def build(
    cfg: Settings,
    s: Store,
    models: Mapping[str, ChatModel],
    citations: Path | None,
    jurisdictions: Iterable[str] | None = None,
    *,
    task: str | None = None,
    force: bool = False,
) -> list[Built]:
    found = [c for c in configured(cfg, models) if task is None or c.task.name == task]
    if not found or not s.exists():
        return []
    names = names_mod.load(citations)
    by_name = {c.task.name: c for c in found}
    with s.open(read_only=True) as conn:
        plans = plan(cfg, conn, found, names, jurisdictions)
        state = steps.classify(cfg, conn, plans)
    results = []
    for p in plans:
        if state[p.key] is steps.Staleness.FRESH and not force:
            continue
        done = build_one(cfg, s, by_name[p.key.scope], p, names)
        if done is not None:
            results.append(done)
    return results


def views(cfg: Settings) -> dict[str, str]:
    found = {}
    for t in tasks.TASKS:
        root = cfg.derived / t.name
        if any(root.glob("*/*.parquet")):
            found[t.name] = (root.relative_to(cfg.data_root) / "*" / "*.parquet").as_posix()
    return found
