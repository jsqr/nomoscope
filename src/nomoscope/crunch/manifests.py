"""Step manifests: a crunch step's graph rows, kept as a file beside its tables.

A manifest is written after its step's tables, so a manifest whose tables
are all present is a finished step. A data root whose graph store lacks the
step adopts it from the manifest instead of running it. The graph store can
therefore be deleted and refilled, and finished steps can be copied between
data roots.
"""

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from nomoscope.crunch.layout import Layout
from nomoscope.graph.steps import Planned, TableRow
from nomoscope.settings import Settings

VERSION = 1


@dataclass(frozen=True)
class Manifest:
    tables: tuple[TableRow, ...]
    details: Mapping[str, object] | None


def path(cfg: Settings, planned: Planned) -> Path:
    k = planned.key
    return Layout(k.scope).manifest(cfg, k.stage, k.unit, planned.fingerprint)


def write(
    cfg: Settings,
    planned: Planned,
    tables: Sequence[TableRow],
    details: Mapping[str, object] | None = None,
) -> Path:
    k = planned.key
    body = {
        "version": VERSION,
        "stage": k.stage,
        "scope": k.scope,
        "unit": k.unit,
        "inputs": [list(i.parts) for i in planned.inputs],
        "code_version": planned.code_version,
        "fingerprint": planned.fingerprint,
        "details": details,
        "tables": [asdict(t) for t in tables],
    }
    target = path(cfg, planned)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def read(cfg: Settings, planned: Planned) -> Manifest | None:
    """The finished step for this plan, or None.

    None when there is no manifest, when it is from another manifest
    version or another step, or when one of its tables is missing.
    """
    found = path(cfg, planned)
    if not found.is_file():
        return None
    body = json.loads(found.read_text(encoding="utf-8"))
    if body.get("version") != VERSION or body.get("fingerprint") != planned.fingerprint:
        return None
    tables = tuple(TableRow(**t) for t in body["tables"])
    if any(not (cfg.data_root / t.path).is_file() for t in tables):
        return None
    return Manifest(tables, body["details"])
