"""Open the graph store. One process writes at a time. Other opens wait for the lock."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

import duckdb

from nomoscope.graph import GraphError
from nomoscope.settings import Settings

LOCK_WAIT = 300.0
LOCK_INTERVAL = 1.0


def schema() -> str:
    return resources.files(__package__).joinpath("schema.sql").read_text(encoding="utf-8")


@dataclass(frozen=True)
class Store:
    cfg: Settings

    @property
    def path(self) -> Path:
        return self.cfg.graph

    def exists(self) -> bool:
        return self.path.is_file()

    @contextmanager
    def open(self, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
        """Return a connection, closed on exit.

        A write-mode open creates the file and applies the schema. A read-only
        open requires the file to exist.
        """
        if read_only and not self.exists():
            raise GraphError(f"no graph store at {self.path}; run crunch first")
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(self.path, read_only=read_only)
        try:
            if not read_only:
                conn.execute(schema())
            yield conn
        finally:
            conn.close()


def connect(path: Path, *, read_only: bool) -> duckdb.DuckDBPyConnection:
    deadline = time.monotonic() + LOCK_WAIT
    while True:
        try:
            return duckdb.connect(str(path), read_only=read_only)
        except duckdb.IOException as e:
            if "lock" not in str(e).lower():
                raise GraphError(f"cannot open graph store {path}: {e}") from e
            if time.monotonic() >= deadline:
                raise GraphError("graph store is locked by another nomoscope process") from e
            time.sleep(LOCK_INTERVAL)
