"""The embed command: vectors for derived segments, billed per token."""

import getpass
from functools import partial
from typing import Annotated

import httpx
import typer

from nomoscope.catalog import build as catalog
from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch.registry import Registry
from nomoscope.embed import client, models, run, text
from nomoscope.errors import NomoscopeError


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command("embed")
    def embed_(
        jurisdiction: Annotated[
            list[str] | None,
            typer.Option("--jurisdiction", "-j", help="Jurisdiction id; repeatable; default all"),
        ] = None,
        spend: Annotated[
            float, typer.Option(help="Dollars this run may spend; 0 only estimates")
        ] = 0.0,
        check: Annotated[
            bool, typer.Option(help="Rebuild each segment's text and check its hash; no calls")
        ] = False,
    ) -> None:
        """Embed the segments not yet embedded, with the embeddings.model setting. Billed per token.

        Without --spend, print what each jurisdiction has pending and its estimated cost.
        """
        cfg = load_settings(name, registry)
        try:
            model = models.configured(cfg.table("embeddings"))
        except NomoscopeError as e:
            raise fail(str(e)) from e
        chosen = jurisdiction or text.jurisdictions(cfg)
        if check:
            bad = 0
            for j in chosen:
                wrong = text.check(cfg, j)
                bad += len(wrong)
                typer.echo(f"{j:28} {len(wrong)} segments whose text does not match its hash")
            if bad:
                raise typer.Exit(1)
            return
        left = spend
        embedded = 0
        http: httpx.Client | None = None
        try:
            for j in chosen:
                todo = run.pending(cfg, model, j)
                if left <= 0 or todo.height == 0:
                    typer.echo(run.estimate(model, todo, j).summary)
                    continue
                http = http or client.make_client(model.provider)
                done = run.embed_pending(
                    cfg,
                    model,
                    j,
                    todo,
                    embed=partial(client.embed, http, model),
                    spend=left,
                    writer=getpass.getuser(),
                )
                left -= done.spent
                embedded += done.embedded
                typer.echo(done.summary)
            if embedded and cfg.catalog.is_file():
                typer.echo(catalog.build(cfg, registry.project_views(cfg)).summary)
        except NomoscopeError as e:
            raise fail(str(e)) from e
        finally:
            if http is not None:
                http.close()
