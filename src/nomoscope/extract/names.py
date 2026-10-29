"""Citation name tables.

`Registry.citations` names a directory with one TOML file per jurisdiction,
such as `us-ny.toml`. A file also applies to the jurisdictions below it. For
a citation from `us-ny-amenia`, `us-ny-amenia.toml` is checked first, then
`us-ny.toml`, then `us.toml`.

    [codes]
    "town law" = "us-ny/law/TWN"
    "vehicle and traffic law" = "us-ny/law/VAT"

    [enactments]
    "local law" = "local-law"
    "l.l." = "local-law"
    "laws of" = "us-ny/chapter-law"

`codes` maps a code's name to the id prefix of its sections, whether or not
the code is loaded. `enactments` maps a word for an enacting instrument to a
kind. A kind prefixed with a jurisdiction belongs to it; a bare kind belongs
to the citing jurisdiction. Names are compared in lower case, with spaces
collapsed and a leading "the" removed.
"""

import hashlib
import json
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from nomoscope.extract import ExtractError

SCOPE = "citations"
TABLES = ("codes", "enactments")


@dataclass(frozen=True)
class Names:
    # jurisdiction -> table -> name -> value
    tables: Mapping[str, Mapping[str, Mapping[str, str]]] = field(default_factory=dict)

    @property
    def fingerprint(self) -> str:
        data = json.dumps(self.tables, sort_keys=True)
        return hashlib.sha256(data.encode()).hexdigest()

    def lookup(self, jurisdiction: str, table: str, name: str | None) -> str | None:
        if not name:
            return None
        key = normalized(name)
        for j in lineage(jurisdiction):
            found = self.tables.get(j, {}).get(table, {}).get(key)
            if found is not None:
                return found
        return None

    def code(self, jurisdiction: str, name: str | None) -> str | None:
        return self.lookup(jurisdiction, "codes", name)

    def enactment(self, jurisdiction: str, word: str | None) -> tuple[str, str] | None:
        found = self.lookup(jurisdiction, "enactments", word)
        if found is None:
            return None
        owner, _, kind = found.rpartition("/")
        return owner or jurisdiction, kind


NONE = Names()


def normalized(name: str) -> str:
    return re.sub(r"^the ", "", re.sub(r"\s+", " ", name.strip().lower()))


def lineage(jurisdiction: str) -> list[str]:
    """us-ny-amenia -> [us-ny-amenia, us-ny, us]"""
    found = [jurisdiction]
    while "-" in found[-1]:
        found.append(found[-1].rpartition("-")[0])
    return found


def load(directory: Path | None) -> Names:
    if directory is None or not directory.is_dir():
        return NONE
    tables = {}
    for path in sorted(directory.glob("*.toml")):
        with path.open("rb") as f:
            raw = tomllib.load(f)
        unknown = sorted(set(raw) - set(TABLES))
        if unknown:
            raise ExtractError(f"{path}: unknown tables {', '.join(unknown)}")
        tables[path.stem] = {
            table: {normalized(k): str(v) for k, v in entries.items()}
            for table, entries in raw.items()
        }
    return Names(tables)
