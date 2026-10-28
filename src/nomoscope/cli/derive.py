"""The derive and catalog commands."""

from typing import Annotated

import typer

from nomoscope.catalog import build as catalog
from nomoscope.cli.common import fail, load_settings
from nomoscope.cli.jurisdictions import FETCH
from nomoscope.crunch.registry import Registry
from nomoscope.derive import build, reference
from nomoscope.errors import NomoscopeError
from nomoscope.graph.store import Store


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command()
    def derive(
        jurisdiction: Annotated[
            list[str] | None,
            typer.Option("--jurisdiction", "-j", help="Jurisdiction id; repeatable"),
        ] = None,
        force: Annotated[bool, typer.Option(help="Refold even if fresh")] = False,
    ) -> None:
        """Build the derived datasets that are stale, then the catalog."""
        cfg = load_settings(name)
        try:
            store = Store(cfg)
            results = [
                *reference.build(cfg, store, force=force),
                *build.build(cfg, store, registry, jurisdiction, force=force),
            ]
        except NomoscopeError as e:
            raise fail(str(e)) from e
        if not reference.census_records(cfg):
            typer.echo(f"jurisdictions: no Census records; fetch them with `{name} {FETCH}`")
        if not results:
            typer.echo("nothing to derive")
        for r in results:
            typer.echo(r.summary)
        if results or not cfg.catalog.is_file():
            typer.echo(catalog.build(cfg, registry.project_views(cfg)).summary)

    @app.command("catalog")
    def catalog_build() -> None:
        """Rebuild the catalog."""
        cfg = load_settings(name)
        typer.echo(catalog.build(cfg, registry.project_views(cfg)).summary)
