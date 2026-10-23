"""The crunch command."""

from typing import Annotated

import typer

from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch import runner
from nomoscope.crunch.registry import Registry
from nomoscope.errors import NomoscopeError
from nomoscope.graph.store import Store


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command()
    def crunch(
        cruncher: Annotated[
            list[str] | None, typer.Argument(help="Cruncher names; default every one")
        ] = None,
        record: Annotated[
            list[str] | None,
            typer.Option("--record", "-r", help="Record id to recrunch; repeatable"),
        ] = None,
        force: Annotated[bool, typer.Option(help="Rerun steps that are fresh")] = False,
    ) -> None:
        """Register, route, and crunch new records."""
        cfg = load_settings(name)
        try:
            results = runner.crunch(
                cfg, Store(cfg), registry, names=cruncher or None, units=record, force=force
            )
        except NomoscopeError as e:
            raise fail(str(e)) from e
        if not results:
            typer.echo("nothing to crunch")
        for r in results:
            typer.echo(r.summary)
