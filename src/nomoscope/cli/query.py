"""The sql, cite, and search commands."""

import json
from dataclasses import asdict
from datetime import date
from typing import Annotated

import duckdb
import polars as pl
import typer

from nomoscope.catalog import build as catalog
from nomoscope.catalog import cite as citing
from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch.registry import Registry
from nomoscope.errors import NomoscopeError
from nomoscope.search import query as searching

FORMATS = ("table", "csv", "json")


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command()
    def sql(
        query: str,
        output: Annotated[str, typer.Option("--format", help="table, csv, or json")] = "table",
        rows: Annotated[int, typer.Option(help="Rows to print as a table")] = 50,
    ) -> None:
        """Run a read-only query against the catalog."""
        cfg = load_settings(name, registry)
        if not cfg.catalog.is_file():
            raise fail(f"no catalog at {cfg.catalog}; run derive or catalog first")
        if output not in FORMATS:
            raise fail(f"--format must be table, csv, or json, not {output!r}")
        conn = catalog.connect(cfg)
        try:
            frame = conn.sql(query).pl()
        except duckdb.Error as e:
            raise fail(str(e)) from e
        finally:
            conn.close()
        if output == "csv":
            typer.echo(frame.write_csv(), nl=False)
        elif output == "json":
            typer.echo(frame.write_ndjson(), nl=False)
        else:
            with pl.Config(
                tbl_rows=rows, tbl_cols=-1, fmt_str_lengths=60, tbl_hide_dataframe_shape=True
            ):
                typer.echo(frame)
            if frame.height > rows:
                typer.echo(f"{frame.height} rows; {rows} shown")

    @app.command()
    def cite(
        jurisdiction: str,
        code: str,
        ref: Annotated[str, typer.Argument(help='A citation, such as "67-14(A)(1)"')],
        on: Annotated[
            str | None, typer.Option(help="The date, YYYY-MM-DD; default today, UTC")
        ] = None,
    ) -> None:
        """Print a section or subdivision as it was in force on a date."""
        cfg = load_settings(name, registry)
        if not cfg.catalog.is_file():
            raise fail(f"no catalog at {cfg.catalog}; run derive or catalog first")
        try:
            when = date.fromisoformat(on) if on else None
        except ValueError as e:
            raise fail(f"--on: {e}") from e
        try:
            found = citing.lookup(cfg, jurisdiction, code, ref, when)
        except duckdb.Error as e:
            raise fail(str(e)) from e
        if found is None:
            raise fail(f"{ref}: not in force in {jurisdiction} {code} on {when or citing.today()}")
        where = f"({found.path.replace('/', ')(')})" if found.path else ""
        level = f"  [{found.level}]" if found.level else ""
        typer.echo(f"{found.section_num}{where}  {found.heading or ''}{level}")
        typer.echo(found.text)
        until = f"{found.end_date} ({found.end_basis})" if found.end_date else "current"
        typer.echo(f"in force {found.start_date} ({found.start_basis}) to {until}")

    @app.command()
    def search(
        query: str,
        jurisdiction: Annotated[str, typer.Option("--jurisdiction", "-j", help="Jurisdiction id")],
        on: Annotated[
            str | None, typer.Option(help="The date, YYYY-MM-DD; default today, UTC")
        ] = None,
        vector: Annotated[
            bool, typer.Option(help="Rank by stored vectors; needs embed and a key")
        ] = True,
        text: Annotated[bool, typer.Option(help="Also rank by BM25 over segment text")] = False,
        rerank: Annotated[
            bool, typer.Option(help="Rerank the top results with the reranker; billed")
        ] = False,
        limit: Annotated[int, typer.Option(help="Sections to print")] = 10,
        output: Annotated[str, typer.Option("--format", help="table or json")] = "table",
    ) -> None:
        """Find the sections in force that answer a query: one hit per section, best first."""
        cfg = load_settings(name, registry)
        if output not in ("table", "json"):
            raise fail(f"--format must be table or json, not {output!r}")
        if not cfg.catalog.is_file():
            raise fail(f"no catalog at {cfg.catalog}; run derive or catalog first")
        try:
            when = date.fromisoformat(on) if on else None
        except ValueError as e:
            raise fail(f"--on: {e}") from e
        try:
            hits = searching.search(
                cfg,
                query,
                jurisdiction,
                on=when,
                vector=vector,
                text=text,
                rerank=rerank,
                limit=limit,
            )
        except (NomoscopeError, duckdb.Error) as e:
            raise fail(str(e)) from e
        if output == "json":
            for h in hits:
                typer.echo(json.dumps({**asdict(h), "citation": h.citation}))
            return
        if not hits:
            typer.echo("no hits")
        for h in hits:
            snippet = " ".join(h.snippet.split())
            typer.echo(f"{h.rank:3}  {h.citation}  {h.heading or ''}  ({h.score:.4f})")
            typer.echo(f"     {snippet[:200]}{'...' if len(snippet) > 200 else ''}")
