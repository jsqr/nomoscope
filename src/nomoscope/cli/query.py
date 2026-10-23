"""The sql command."""

from typing import Annotated

import duckdb
import polars as pl
import typer

from nomoscope.catalog import build as catalog
from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch.registry import Registry

FORMATS = ("table", "csv", "json")


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command()
    def sql(
        query: str,
        output: Annotated[str, typer.Option("--format", help="table, csv, or json")] = "table",
        rows: Annotated[int, typer.Option(help="Rows to print as a table")] = 50,
    ) -> None:
        """Run a read-only query against the catalog."""
        cfg = load_settings(name)
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
