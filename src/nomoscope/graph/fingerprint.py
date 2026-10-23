"""Hashes of columns, step inputs, steps, and files."""

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import BinaryIO

import polars as pl

Declared = pl.Schema | Sequence[str]
CHUNK = 1 << 20


def hash_stream(reader: BinaryIO) -> tuple[str, int]:
    """Return the sha256 of the reader's bytes, and the byte count."""
    h = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: reader.read(CHUNK), b""):
        h.update(chunk)
        size += len(chunk)
    return h.hexdigest(), size


def columns_fingerprint(*declared: Declared) -> str:
    """Return 16 hex digits over the (name, dtype) pairs of the columns.

    A list of names hashes with empty dtypes.
    """
    columns: list[list[str]] = []
    for d in declared:
        if isinstance(d, pl.Schema):
            columns.extend([name, str(dtype)] for name, dtype in d.items())
        else:
            columns.extend([name, ""] for name in d)
    return hashlib.sha256(json.dumps(columns).encode()).hexdigest()[:16]


def code_version(version: str, *declared: Declared) -> str:
    """Return the version, with the columns' fingerprint appended if any are given."""
    return f"{version}.{columns_fingerprint(*declared)}" if declared else version


def input_fingerprint(inputs: Iterable[tuple[str, str, str, str]]) -> str:
    """Hash (stage, scope, unit, fingerprint) tuples in order."""
    lines = "\n".join(
        f"{stage}/{scope}/{unit}={fingerprint}" for stage, scope, unit, fingerprint in inputs
    )
    return hashlib.sha256(lines.encode()).hexdigest()


def step_fingerprint(input_fingerprint: str, code_version: str) -> str:
    return hashlib.sha256(f"{input_fingerprint}\n{code_version}".encode()).hexdigest()


def file_sha256(path: Path) -> str:
    with path.open("rb") as f:
        sha256, _ = hash_stream(f)
    return sha256
