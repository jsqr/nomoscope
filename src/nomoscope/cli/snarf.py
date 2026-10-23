"""The snarf commands."""

from pathlib import Path
from typing import Annotated

import typer

from nomoscope.cli.common import Params, fail, load_settings, parse_params
from nomoscope.crunch.registry import Registry
from nomoscope.errors import NomoscopeError
from nomoscope.settings import Settings
from nomoscope.snarf import http, local, run
from nomoscope.snarf.feeds import Source


def fetch_feeds(
    cfg: Settings,
    src: Source,
    names: list[str],
    params: dict[str, str],
    *,
    force: bool = False,
    limit: int | None = None,
    delay: float | None = None,
) -> list[run.Outcome]:
    outcomes = []
    with http.make_client(cfg.user_agent, src.request_headers(cfg)) as client:
        for feed_name in dict.fromkeys(names):
            outcomes.extend(
                run.snarf_feed(
                    cfg,
                    src,
                    src.feed(feed_name),
                    params,
                    client=client,
                    force=force,
                    limit=limit,
                    delay=delay,
                )
            )
    return outcomes


def report(outcomes: list[run.Outcome]) -> None:
    for o in outcomes:
        state = o.record.id if o.record else f"HTTP {o.status}, nothing to do"
        typer.echo(f"{o.feed:12} {state}  {o.url}")


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    group = typer.Typer(no_args_is_help=True, help="Fetch or import into sources/snarfed/.")
    app.add_typer(group, name="snarf")

    @group.command("fetch")
    def fetch(
        source: str,
        feed: Annotated[
            list[str] | None, typer.Option("--feed", "-f", help="Feed name; repeatable")
        ] = None,
        due: Annotated[bool, typer.Option(help="The feeds the source says are due today")] = False,
        cold_start: Annotated[bool, typer.Option(help="The source's cold-start feeds")] = False,
        param: Params = None,
        force: Annotated[bool, typer.Option(help="Download even if unchanged")] = False,
        limit: Annotated[int | None, typer.Option(help="Stop after this many files")] = None,
        delay: Annotated[
            float | None,
            typer.Option(help="Seconds between requests; overrides the feed's"),
        ] = None,
    ) -> None:
        """Fetch a source's feeds."""
        cfg = load_settings(name)
        try:
            src = registry.source(source)
            today = src.today()
            names = list(feed or [])
            if due:
                names.extend(src.due(today))
            if cold_start:
                names.extend(src.cold_start)
            if not names:
                raise fail("choose feeds: --feed NAME, --due, or --cold-start")
            params = src.defaults(today) | parse_params(param or [])
            outcomes = fetch_feeds(cfg, src, names, params, force=force, limit=limit, delay=delay)
        except NomoscopeError as e:
            raise fail(str(e)) from e
        report(outcomes)

    @group.command("due")
    def due() -> None:
        """Fetch every source's feeds that are due today."""
        cfg = load_settings(name)
        try:
            for src in registry.sources.values():
                today = src.today()
                names = list(src.due(today))
                if names:
                    report(fetch_feeds(cfg, src, names, src.defaults(today)))
        except NomoscopeError as e:
            raise fail(str(e)) from e

    @group.command("import")
    def import_(
        source: str,
        path: Path,
        feed: Annotated[str, typer.Option("--feed", "-f", help="The feed the files belong to")],
        param: Params = None,
        importer: Annotated[
            str | None,
            typer.Option(help="Who obtained the files; default the current user"),
        ] = None,
        force: Annotated[bool, typer.Option(help="Import even if unchanged")] = False,
    ) -> None:
        """Archive a local file or directory as one record."""
        cfg = load_settings(name)
        if not path.exists():
            raise fail(f"no such file or directory: {path}")
        try:
            src = registry.source(source)
            params = src.defaults(src.today()) | parse_params(param or [])
            o = local.import_path(
                cfg, src, src.feed(feed), path, params, importer=importer, force=force
            )
        except NomoscopeError as e:
            raise fail(str(e)) from e
        state = o.record.id if o.record else "unchanged, nothing to do"
        typer.echo(f"{o.feed:12} {state}  {o.url}")
