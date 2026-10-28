"""Helpers shared by the command modules."""

from typing import Annotated

import typer

from nomoscope import settings
from nomoscope.crunch.registry import Registry
from nomoscope.settings import Settings


def fail(message: str) -> typer.Exit:
    typer.secho(message, fg=typer.colors.RED, err=True)
    return typer.Exit(1)


def load_settings(name: str, registry: Registry) -> Settings:
    """The settings, with the registry's project file under the machine's."""
    try:
        return settings.load(name, project=registry.settings, tables=registry.tables)
    except settings.SettingsError as e:
        raise fail(str(e)) from e


def parse_params(pairs: list[str]) -> dict[str, str]:
    params = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise fail(f"--param wants KEY=VALUE, not {pair!r}")
        params[key] = value
    return params


Params = Annotated[list[str] | None, typer.Option("--param", "-p", help="KEY=VALUE; repeatable")]
