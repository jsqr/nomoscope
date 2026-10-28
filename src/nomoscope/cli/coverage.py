"""The coverage commands: list the jurisdictions a project covers, and set or remove one."""

import getpass
from datetime import datetime
from typing import Annotated

import polars as pl
import typer

from nomoscope import coverage
from nomoscope.catalog import build as catalog
from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch import runner
from nomoscope.crunch.registry import Registry
from nomoscope.derive import registry as datasets
from nomoscope.errors import NomoscopeError
from nomoscope.graph.store import Store
from nomoscope.partition import reference_path
from nomoscope.settings import Settings

Jurisdiction = Annotated[str, typer.Argument(help="Jurisdiction id")]
By = Annotated[str | None, typer.Option(help="Who writes the row; default the current user")]
Note = Annotated[str | None, typer.Option(help="A note on the row")]


def known(cfg: Settings, jurisdiction: str) -> None:
    path = reference_path(cfg, datasets.JURISDICTIONS)
    if not path.is_file():
        raise fail("no jurisdictions table; fetch the Census files and run derive")
    ids = pl.read_parquet(path, columns=["id"])["id"]
    if jurisdiction not in ids:
        raise fail(f"{jurisdiction}: not in the jurisdictions table")


def fetches(cfg: Settings, store: Store, known_sources: Registry) -> pl.DataFrame:
    """Each jurisdiction's first and latest fetch of a code, from the archive."""
    runner.register(cfg, store, known_sources)
    with store.open(read_only=True) as conn:
        found = conn.execute(
            "SELECT jurisdiction, min(fetched_at), max(fetched_at), count(*) "
            "FROM records WHERE crunchable GROUP BY jurisdiction"
        ).fetchall()
    return pl.DataFrame(
        found,
        schema={
            "jurisdiction": pl.String,
            "first_fetch": pl.Datetime("us"),
            "latest_fetch": pl.Datetime("us"),
            "records": pl.Int64,
        },
        orient="row",
    )


def day(when: datetime | None) -> str:
    return when.date().isoformat() if when else "-"


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    group = typer.Typer(help="The jurisdictions the project covers.")
    app.add_typer(group, name="coverage")

    def written(cfg: Settings, entry: coverage.Entry, by: str | None) -> None:
        try:
            coverage.write(cfg, [entry], by or getpass.getuser())
        except NomoscopeError as e:
            raise fail(str(e)) from e
        catalog.build(cfg, registry.project_views(cfg))
        state = "listed" if entry.listed else "removed"
        typer.echo(f"{entry.jurisdiction} {state}: {entry.publisher or '-'} {entry.url or '-'}")

    @group.callback(invoke_without_command=True)
    def list_(
        ctx: typer.Context,
        state: Annotated[str | None, typer.Option(help="A state's postal code, such as ny")] = None,
    ) -> None:
        """Print the listed jurisdictions, with publisher, URL, and first and latest fetch."""
        if ctx.invoked_subcommand is not None:
            return
        cfg = load_settings(name, registry)
        listed = coverage.in_force(cfg)
        if state is not None:
            listed = listed.filter(pl.col("jurisdiction").str.starts_with(f"us-{state.lower()}-"))
        try:
            found = fetches(cfg, Store(cfg), registry)
        except NomoscopeError as e:
            raise fail(str(e)) from e
        joined = listed.join(found, on="jurisdiction", how="left")
        for r in joined.iter_rows(named=True):
            typer.echo(
                f"{r['jurisdiction']:40} {r['publisher'] or '-':12} "
                f"{day(r['first_fetch']):10} {day(r['latest_fetch']):10} {r['url'] or '-'}"
            )
        fetched = joined.filter(pl.col("records").is_not_null()).height
        typer.echo(f"{joined.height} listed, {fetched} fetched", err=True)

    @group.command("set")
    def set_(
        jurisdiction: Jurisdiction,
        publisher: Annotated[
            str | None, typer.Option(help="A source id, or the publisher's name")
        ] = None,
        url: Annotated[str | None, typer.Option(help="The code's URL")] = None,
        by: By = None,
        note: Note = None,
    ) -> None:
        """List a jurisdiction, or change its publisher or URL. Values not given are kept."""
        cfg = load_settings(name, registry)
        known(cfg, jurisdiction)
        entry = coverage.change(cfg, jurisdiction, publisher=publisher, url=url, note=note)
        written(cfg, entry, by)

    @group.command("match")
    def match(
        dry_run: Annotated[bool, typer.Option(help="Print the matches; write nothing")] = False,
        by: By = None,
    ) -> None:
        """Fill in publishers and URLs from the project's listings.

        A listed jurisdiction that already has a publisher is left alone. A
        jurisdiction with one listing of its state, name, and kind gets it; the
        rest are printed to settle with `coverage set`.
        """
        cfg = load_settings(name, registry)
        if registry.listings is None:
            raise fail("the project gives no listings")
        path = reference_path(cfg, datasets.JURISDICTIONS)
        if not path.is_file():
            raise fail("no jurisdictions table; fetch the Census files and run derive")
        try:
            found = registry.listings(cfg)
        except NomoscopeError as e:
            raise fail(str(e)) from e
        listed = coverage.in_force(cfg)
        open_ = listed.filter(pl.col("publisher").is_null())["jurisdiction"]
        notes = dict(zip(listed["jurisdiction"], listed["note"], strict=True))
        matches = coverage.match(found, pl.read_parquet(path), open_)
        # The listed row's note is kept, so the row in force still carries it.
        entries = [
            coverage.Entry(
                j,
                publisher=listing.publisher,
                url=listing.url,
                note="; ".join(
                    n for n in (notes[j], f"matched {listing.name!r} in {listing.record}") if n
                ),
            )
            for j, listing in matches.exact
        ]
        for e in entries:
            typer.echo(f"{e.jurisdiction:40} {e.publisher:16} {e.url}")
        for j, candidates in matches.ambiguous:
            choices = "; ".join(f"{c.name} ({c.publisher} {c.url})" for c in candidates)
            typer.echo(f"{j:40} ambiguous: {choices}")
        typer.echo(
            f"{len(found)} listings; {len(entries)} matched, {len(matches.ambiguous)} ambiguous, "
            f"{len(open_) - len(entries) - len(matches.ambiguous)} without a listing",
            err=True,
        )
        if dry_run or not entries:
            return
        try:
            coverage.write(cfg, entries, by or getpass.getuser())
        except NomoscopeError as e:
            raise fail(str(e)) from e
        catalog.build(cfg, registry.project_views(cfg))

    @group.command("remove")
    def remove(jurisdiction: Jurisdiction, by: By = None, note: Note = None) -> None:
        """Take a jurisdiction off the list. Its publisher and URL are kept."""
        cfg = load_settings(name, registry)
        was = coverage.current(cfg, jurisdiction)
        if was is None or not was.listed:
            raise fail(f"{jurisdiction}: not listed")
        written(cfg, coverage.change(cfg, jurisdiction, listed=False, note=note), by)
