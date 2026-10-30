"""Settings for a project that runs the pipeline.

For a project named app, the settings file is the first of:

    $APP_CONFIG
    app.toml in the working directory or a parent
    ~/.config/app/config.toml

$APP_DATA_ROOT overrides data_root, and is enough without a file. There is
no default data root.

A project may also commit a settings file of its own (`Registry.settings`)
for choices that change what the pipeline builds, such as a stage's
model. The machine's file is read over it, key by key, so a machine can
override a project value. `origins` records which file each value came
from. Top-level keys are nomoscope's and the tables the project names
(`Registry.tables`); any other is an error. Keys stay in the environment;
a file names the variable.
"""

import copy
import os
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nomoscope.errors import NomoscopeError

USER_CONFIG_DIR = Path("~/.config")
DEFAULT_USER_AGENT = "nomoscope/0.1.0 (+https://github.com/jsqr/nomoscope)"
# Top-level keys nomoscope reads. Projects add their tables.
# definitions through enactments are the extraction tasks (`extract/tasks.py`).
KEYS = frozenset(
    {
        "data_root",
        "user_agent",
        "sources",
        "ocr",
        "embeddings",
        "search",
        "answers",
        "definitions",
        "interpretation",
        "penalties",
        "references",
        "enactments",
        "lookups",
    }
)


# Settings that no longer exist, and what replaced them.
MOVED = {
    "config_root": "grammars and conventions are directories in the project's registry, "
    "and its routes are values there",
    "segments": "the segment settings are in the project's registry",
}


class SettingsError(NomoscopeError):
    pass


def env_config(app: str) -> str:
    return f"{app.upper()}_CONFIG"


def env_data_root(app: str) -> str:
    return f"{app.upper()}_DATA_ROOT"


def config_basename(app: str) -> str:
    return f"{app}.toml"


@dataclass(frozen=True)
class Settings:
    data_root: Path
    app: str = "nomoscope"
    user_agent: str = DEFAULT_USER_AGENT
    source_settings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    path: Path | None = None
    project_path: Path | None = None
    tables: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    # Dotted key -> the file its value came from.
    origins: Mapping[str, Path] = field(default_factory=dict)

    def table(self, name: str) -> Mapping[str, Any]:
        """A top-level table, empty if no file sets it."""
        return self.tables.get(name, {})

    def secret(self, source: str, name: str = "key_env") -> str:
        """Return the value of the environment variable named by
        [sources.<source>] <name> in the settings file.
        """
        var = self.source_settings.get(source, {}).get(name)
        if not var:
            raise SettingsError(f"[sources.{source}] sets no {name}")
        value = os.environ.get(var)
        if not value:
            raise SettingsError(f"{var} is not set (named by [sources.{source}] {name})")
        return value

    @property
    def sources(self) -> Path:
        return self.data_root / "sources"

    @property
    def snarfed(self) -> Path:
        """The archive. Never changed or deleted."""
        return self.sources / "snarfed"

    @property
    def crunched(self) -> Path:
        return self.sources / "crunched"

    @property
    def derived(self) -> Path:
        return self.data_root / "derived"

    @property
    def staging(self) -> Path:
        """Downloads in progress. Safe to delete."""
        return self.data_root / "staging"

    @property
    def graph(self) -> Path:
        return self.data_root / "graph.duckdb"

    @property
    def catalog(self) -> Path:
        return self.data_root / "catalog.duckdb"

    @property
    def tiers(self) -> tuple[Path, ...]:
        return (self.snarfed, self.crunched, self.derived, self.staging)


def search_paths(app: str) -> list[Path]:
    env = os.environ.get(env_config(app))
    paths = [Path(env).expanduser()] if env else []
    cwd = Path.cwd().resolve()
    paths.extend(d / config_basename(app) for d in (cwd, *cwd.parents))
    paths.append((USER_CONFIG_DIR / app / "config.toml").expanduser())
    return paths


def find_config_file(app: str) -> Path | None:
    return next((p for p in search_paths(app) if p.is_file()), None)


def read(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise SettingsError(f"{path}: {e}") from e


def layered(
    layers: Iterable[tuple[Path, Mapping[str, Any]]],
) -> tuple[dict[str, Any], dict[str, Path]]:
    """Merge settings files, later over earlier. Tables merge key by key; other values replace.

    Returns the merged settings and the file each dotted key came from.
    """
    merged: dict[str, Any] = {}
    origins: dict[str, Path] = {}
    for path, raw in layers:
        _merge(merged, raw, path, origins, "")
    return merged, origins


def _merge(
    into: dict[str, Any],
    raw: Mapping[str, Any],
    path: Path,
    origins: dict[str, Path],
    prefix: str,
) -> None:
    for key, value in raw.items():
        dotted = prefix + key
        if isinstance(value, Mapping) and isinstance(into.get(key), dict):
            _merge(into[key], value, path, origins, dotted + ".")
            continue
        into[key] = copy.deepcopy(value)
        for old in [k for k in origins if k == dotted or k.startswith(dotted + ".")]:
            del origins[old]
        for leaf in _leaves(value, dotted):
            origins[leaf] = path


def flat(tables: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Each value under its dotted key, for display."""
    out: dict[str, Any] = {}
    for key, value in tables.items():
        if isinstance(value, Mapping):
            out.update(flat(value, f"{prefix}{key}."))
        else:
            out[prefix + key] = value
    return out


def _leaves(value: Any, dotted: str) -> list[str]:
    if isinstance(value, Mapping) and value:
        return [leaf for k, v in value.items() for leaf in _leaves(v, f"{dotted}.{k}")]
    return [dotted]


def load(
    app: str,
    path: Path | None = None,
    *,
    project: Path | None = None,
    tables: Iterable[str] = (),
) -> Settings:
    """Resolve the settings: the project's file, then the machine's over it.

    `path` is the machine's file, found by search when not given. `project`
    is the project's committed file. `tables` are the top-level tables the
    project reads.
    """
    if path is None:
        path = find_config_file(app)
    elif not path.is_file():
        raise SettingsError(f"settings file not found: {path}")
    if project is not None and not project.is_file():
        raise SettingsError(f"project settings file not found: {project}")

    layers = [(f, read(f)) for f in (project, path) if f is not None]
    for f, found in layers:
        for key, moved in MOVED.items():
            if key in found:
                raise SettingsError(f"{f}: {key} is no longer a setting; {moved}")
    raw, origins = layered(layers)

    known = KEYS | set(tables)
    unknown = sorted(set(raw) - known)
    if unknown:
        where = sorted({str(f) for k, f in origins.items() if k.split(".")[0] in unknown})
        raise SettingsError(
            f"unknown setting {', '.join(unknown)} in {', '.join(where)};"
            f" known: {', '.join(sorted(known))}"
        )

    env_root = os.environ.get(env_data_root(app))
    data_root = env_root or raw.get("data_root")
    if not data_root:
        searched = "\n  ".join(str(p) for p in search_paths(app))
        raise SettingsError(
            f"no data root: set {env_data_root(app)}, or set data_root in a settings file.\n"
            f"Searched:\n  {searched}"
        )
    if env_root:
        origins["data_root"] = Path(f"${env_data_root(app)}")

    return Settings(
        data_root=Path(data_root).expanduser().resolve(),
        app=app,
        user_agent=raw.get("user_agent", DEFAULT_USER_AGENT),
        source_settings=raw.get("sources", {}),
        path=path,
        project_path=project,
        tables={k: v for k, v in raw.items() if isinstance(v, Mapping) and k != "sources"},
        origins=origins,
    )
