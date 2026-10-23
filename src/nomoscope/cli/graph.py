"""The settings, status, and graph show commands."""

import typer

from nomoscope import settings, show, status
from nomoscope.cli.common import load_settings
from nomoscope.crunch.registry import Registry


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command("settings")
    def settings_show() -> None:
        """Show the resolved settings."""
        cfg = load_settings(name)
        typer.echo(f"settings:    {cfg.path or '(none; ' + settings.env_data_root(name) + ')'}")
        typer.echo(f"data_root:   {cfg.data_root}")
        typer.echo(f"config_root: {cfg.config_root or '(none)'}")
        typer.echo(f"user_agent:  {cfg.user_agent}")
        typer.echo(f"graph:       {cfg.graph}")
        typer.echo(f"catalog:     {cfg.catalog}")
        typer.echo(f"sources:     {', '.join(sorted(registry.sources)) or '(none)'}")
        typer.echo(f"crunchers:   {', '.join(sorted(registry.crunchers)) or '(none)'}")

    @app.command("status")
    def status_show() -> None:
        """Show what each source, cruncher, and jurisdiction has been through."""
        report = status.status(load_settings(name), registry)
        typer.echo(f"data_root: {report.data_root}")
        typer.echo(f"catalog:   {'built' if report.catalog else 'not built'}")
        typer.echo("")
        typer.echo(f"{'source':24} {'archived':>8} {'registered':>10} {'unrouted':>8}")
        for s in report.sources:
            note = "" if s.known else "  (not in the registry)"
            typer.echo(f"{s.source:24} {s.archived:8} {s.registered:10} {s.unrouted:8}{note}")
        if report.crunchers:
            typer.echo("")
            typer.echo(f"{'cruncher':24} steps")
        for c in report.crunchers:
            states = ", ".join(f"{k} {n}" for k, n in sorted(c.states.items()))
            typer.echo(f"{c.cruncher:24} {states}")
        if report.jurisdictions:
            typer.echo("")
            typer.echo(f"{'jurisdiction':24} {'derive':8} {'pubs':>5} {'versions':>9}  crunchers")
        for j in report.jurisdictions:
            versions = "?" if j.versions is None else str(j.versions)
            typer.echo(
                f"{j.jurisdiction:24} {j.state:8} {j.publications:5} {versions:>9}"
                f"  {', '.join(j.crunchers)}"
            )

    graph = typer.Typer(no_args_is_help=True, help="The graph store.")
    app.add_typer(graph, name="graph")

    @graph.command("show")
    def graph_show() -> None:
        """List every planned step and its state."""
        lines = show.listing(load_settings(name), registry)
        if not lines:
            typer.echo("no graph store yet")
        for line in lines:
            step = "/".join(p for p in (line.stage, line.scope, line.unit) if p)
            typer.echo(f"{line.state:8} {step}  ({line.inputs} inputs)")
