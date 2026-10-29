"""Steps: what a stage ran for one unit, what it read, and what it wrote.

A step reruns when:

- it has no row;
- its code version changed;
- the fingerprint of its inputs changed;
- an output file is missing.

A step's fingerprint depends only on its plan. So when an upstream step
will rerun, the downstream step is already stale before either runs.
"""

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

import duckdb
import polars as pl

from nomoscope.graph import fingerprint, records
from nomoscope.graph.records import RECORD, RecordRow
from nomoscope.settings import Settings

CRUNCH = "crunch"
TABULATE = "tabulate"
DERIVE = "derive"
REFERENCE = "reference"
EXTRACT = "extract"
# An extraction store as an input; not a step.
EXTRACTIONS = "extractions"
# A config file read by a step; not a step itself.
CONFIG = "config"


class Staleness(Enum):
    FRESH = "fresh"
    MISSING = "missing"
    INPUT = "input"
    CODE = "code"
    OUTPUT = "output"


@dataclass(frozen=True)
class StepKey:
    stage: str
    scope: str
    unit: str


@dataclass(frozen=True)
class Input:
    stage: str
    scope: str
    unit: str
    fingerprint: str

    @property
    def parts(self) -> tuple[str, str, str, str]:
        return self.stage, self.scope, self.unit, self.fingerprint


def record_input(row: RecordRow) -> Input:
    return Input(RECORD, row.source, row.record, row.sha256)


@dataclass(frozen=True)
class Planned:
    key: StepKey
    inputs: tuple[Input, ...]
    code_version: str

    @property
    def input_fingerprint(self) -> str:
        return fingerprint.input_fingerprint(i.parts for i in self.inputs)

    @property
    def fingerprint(self) -> str:
        return fingerprint.step_fingerprint(self.input_fingerprint, self.code_version)

    @property
    def input(self) -> Input:
        """This step as the input of a downstream step."""
        return Input(self.key.stage, self.key.scope, self.key.unit, self.fingerprint)


@dataclass(frozen=True)
class TableRow:
    name: str
    path: str
    rows: int
    columns_fingerprint: str
    bytes: int
    sha256: str | None = None


@dataclass(frozen=True)
class Stored:
    key: StepKey
    input_fingerprint: str
    code_version: str
    fingerprint: str
    ran_at: datetime
    details: Mapping[str, object] | None = None


def admitted(step: Stored) -> bool:
    """Whether derive may read a tabulation: one that records admitted false is held."""
    return not step.details or step.details.get("admitted") is not False


def table_row(
    cfg: Settings,
    name: str,
    path: Path,
    schema: pl.Schema,
    rows: int,
    *,
    sha256: str | None = None,
) -> TableRow:
    return TableRow(
        name=name,
        path=str(path.relative_to(cfg.data_root)),
        rows=rows,
        columns_fingerprint=fingerprint.columns_fingerprint(schema),
        bytes=path.stat().st_size,
        sha256=sha256,
    )


def staleness(
    cfg: Settings,
    planned: Planned,
    row: Stored | None,
    tables: Iterable[TableRow],
    *,
    check_outputs: bool = True,
) -> Staleness:
    if row is None:
        return Staleness.MISSING
    if row.code_version != planned.code_version:
        return Staleness.CODE
    if row.input_fingerprint != planned.input_fingerprint:
        return Staleness.INPUT
    if check_outputs and any(not (cfg.data_root / t.path).exists() for t in tables):
        return Staleness.OUTPUT
    return Staleness.FRESH


def classify(
    cfg: Settings,
    conn: duckdb.DuckDBPyConnection,
    planned: Iterable[Planned],
    *,
    check_outputs: bool = True,
) -> dict[StepKey, Staleness]:
    """Return the staleness of each planned step.

    With check_outputs, every recorded output file is checked for existence.
    Turn it off to report only what the store records.
    """
    by_scope: dict[tuple[str, str], list[Planned]] = defaultdict(list)
    for p in planned:
        by_scope[p.key.stage, p.key.scope].append(p)
    result = {}
    for (stage, scope), plans in by_scope.items():
        rows = stored(conn, stage, scope)
        tables = tables_of(conn, stage, scope)
        for p in plans:
            unit = p.key.unit
            result[p.key] = staleness(
                cfg,
                p,
                rows.get(unit),
                tables.get(unit, []),
                check_outputs=check_outputs,
            )
    return result


def stored(conn: duckdb.DuckDBPyConnection, stage: str, scope: str) -> dict[str, Stored]:
    rows = conn.execute(
        "SELECT unit, input_fingerprint, code_version, fingerprint, ran_at, details"
        " FROM steps WHERE stage = ? AND scope = ?",
        [stage, scope],
    ).fetchall()
    return {
        unit: Stored(
            StepKey(stage, scope, unit),
            inp,
            code,
            fp,
            records.from_db(ran_at),
            json.loads(details) if details is not None else None,
        )
        for unit, inp, code, fp, ran_at, details in rows
    }


def tables_of(conn: duckdb.DuckDBPyConnection, stage: str, scope: str) -> dict[str, list[TableRow]]:
    rows = conn.execute(
        "SELECT unit, name, path, rows, columns_fingerprint, bytes, sha256"
        " FROM tables WHERE stage = ? AND scope = ? ORDER BY unit, name",
        [stage, scope],
    ).fetchall()
    found: dict[str, list[TableRow]] = defaultdict(list)
    for unit, name, path, n, columns, size, sha256 in rows:
        found[unit].append(TableRow(name, path, n, columns, size, sha256))
    return dict(found)


def tables(conn: duckdb.DuckDBPyConnection, key: StepKey) -> list[TableRow]:
    return tables_of(conn, key.stage, key.scope).get(key.unit, [])


def record(
    conn: duckdb.DuckDBPyConnection,
    planned: Planned,
    tables: Iterable[TableRow],
    details: Mapping[str, object] | None = None,
) -> None:
    """Replace the step's row, inputs, and tables in one transaction."""
    k = planned.key
    where = " WHERE stage = ? AND scope = ? AND unit = ?"
    ident = [k.stage, k.scope, k.unit]
    now = records.to_db(datetime.now(UTC))
    conn.begin()
    try:
        for table in ("tables", "step_inputs", "steps"):
            conn.execute(f"DELETE FROM {table}{where}", ident)
        conn.execute(
            "INSERT INTO steps (stage, scope, unit, input_fingerprint, code_version,"
            " fingerprint, ran_at, details) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                *ident,
                planned.input_fingerprint,
                planned.code_version,
                planned.fingerprint,
                now,
                json.dumps(details) if details is not None else None,
            ],
        )
        edges = [(*ident, n, *i.parts) for n, i in enumerate(planned.inputs)]
        if edges:
            conn.executemany(
                "INSERT INTO step_inputs (stage, scope, unit, ordinal, input_stage,"
                " input_scope, input_unit, fingerprint) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                edges,
            )
        rows = [
            (*ident, t.name, t.path, t.rows, t.columns_fingerprint, t.sha256, t.bytes)
            for t in tables
        ]
        if rows:
            conn.executemany(
                "INSERT INTO tables (stage, scope, unit, name, path, rows,"
                " columns_fingerprint, sha256, bytes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def inputs_of(conn: duckdb.DuckDBPyConnection, key: StepKey) -> list[Input]:
    rows = conn.execute(
        "SELECT input_stage, input_scope, input_unit, fingerprint FROM step_inputs"
        " WHERE stage = ? AND scope = ? AND unit = ? ORDER BY ordinal",
        [key.stage, key.scope, key.unit],
    ).fetchall()
    return [Input(*r) for r in rows]


def units(conn: duckdb.DuckDBPyConnection, stage: str, scope: str) -> list[str]:
    rows = conn.execute(
        "SELECT unit FROM steps WHERE stage = ? AND scope = ? ORDER BY unit",
        [stage, scope],
    ).fetchall()
    return [r[0] for r in rows]


def table_hashes(conn: duckdb.DuckDBPyConnection, stage: str) -> dict[str, str]:
    """Map each table path the stage hashed to its sha256."""
    rows = conn.execute(
        "SELECT path, sha256 FROM tables WHERE stage = ? AND sha256 IS NOT NULL",
        [stage],
    ).fetchall()
    return dict(rows)
