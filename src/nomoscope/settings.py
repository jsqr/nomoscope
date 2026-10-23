"""Settings for a project that runs the pipeline.

For a project named app, the settings file is the first of:

    $APP_CONFIG
    app.toml in the working directory or a parent
    ~/.config/app/config.toml

$APP_DATA_ROOT overrides data_root, and is enough without a file. There is
no default data root. A relative config_root is relative to the settings
file.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nomoscope.errors import NomoscopeError

USER_CONFIG_DIR = Path("~/.config")
DEFAULT_USER_AGENT = "nomoscope/0.1.0 (+https://github.com/jsqr/nomoscope)"


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
    config_root: Path | None = None
    user_agent: str = DEFAULT_USER_AGENT
    source_settings: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    path: Path | None = None
    segments: Mapping[str, Any] = field(default_factory=dict)

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


def load(app: str, path: Path | None = None) -> Settings:
    if path is None:
        path = find_config_file(app)
    elif not path.is_file():
        raise SettingsError(f"settings file not found: {path}")

    raw: dict[str, Any] = {}
    if path is not None:
        with path.open("rb") as f:
            raw = tomllib.load(f)

    data_root = os.environ.get(env_data_root(app)) or raw.get("data_root")
    if not data_root:
        searched = "\n  ".join(str(p) for p in search_paths(app))
        raise SettingsError(
            f"no data root: set {env_data_root(app)}, or set data_root in a settings file.\n"
            f"Searched:\n  {searched}"
        )
    config_root = raw.get("config_root")
    if config_root is not None:
        base = path.parent if path is not None else Path.cwd()
        config_root = (base / Path(config_root).expanduser()).resolve()

    return Settings(
        data_root=Path(data_root).expanduser().resolve(),
        app=app,
        config_root=config_root,
        user_agent=raw.get("user_agent", DEFAULT_USER_AGENT),
        source_settings=raw.get("sources", {}),
        path=path,
        segments=raw.get("segments", {}),
    )
