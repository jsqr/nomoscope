"""The jurisdictions command: look up a jurisdiction's id in the Census table."""

from typing import Annotated

import polars as pl
import typer

from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch.registry import Registry
from nomoscope.derive import reference
from nomoscope.errors import NomoscopeError

FETCH = "snarf fetch census -f states -f counties -f places -f cousubs -p year=<year>"


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    group = typer.Typer(no_args_is_help=True, help="Look up jurisdiction ids.")
    app.add_typer(group, name="jurisdictions")

    @group.command("find")
    def find(
        text: Annotated[str, typer.Argument(help="A name, or part of one")],
        state: Annotated[str | None, typer.Option(help="A state's postal code, such as ny")] = None,
    ) -> None:
        """Print the jurisdictions whose name contains TEXT, from the Census records."""
        cfg = load_settings(name, registry)
        found = reference.census_records(cfg)
        if not found:
            raise fail(f"no Census records; fetch them with `{name} {FETCH}`")
        try:
            frame = reference.jurisdictions(cfg, found)
        except NomoscopeError as e:
            raise fail(str(e)) from e
        hits = frame.filter(
            pl.col("name").str.to_lowercase().str.contains(text.lower(), literal=True)
        )
        if state is not None:
            hits = hits.filter(pl.col("parent") == f"us-{state.lower()}")
        if hits.is_empty():
            typer.echo("no match")
        for r in hits.sort("id").iter_rows(named=True):
            typer.echo(f"{r['id']:44} {r['name']} ({r['kind']}, GEOID {r['geoid']})")
