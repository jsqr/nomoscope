"""The extract command. Billed per token."""

import getpass
from functools import partial
from typing import Annotated, Any

import httpx
import typer

from nomoscope import chat
from nomoscope.catalog import build as catalog
from nomoscope.cli.common import catalog_views, fail, load_settings
from nomoscope.crunch.registry import Registry
from nomoscope.derive import registry as datasets
from nomoscope.errors import NomoscopeError
from nomoscope.extract import build, rows, run, tasks
from nomoscope.extract import task as task_mod
from nomoscope.graph.store import Store
from nomoscope.providers import make_client
from nomoscope.settings import Settings


def jurisdictions(cfg: Settings) -> list[str]:
    root = cfg.derived / datasets.LAW_VERSIONS
    return sorted(p.parent.name.split("=", 1)[1] for p in root.glob("jurisdiction=*/*.parquet"))


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command("extract")
    def extract_(
        task: Annotated[str, typer.Argument(help=f"One of {', '.join(tasks.BY_NAME)}")],
        jurisdiction: Annotated[
            list[str] | None,
            typer.Option("--jurisdiction", "-j", help="Jurisdiction id; repeatable; default all"),
        ] = None,
        spend: Annotated[
            float, typer.Option(help="Dollars this run may spend; 0 only estimates")
        ] = 0.0,
    ) -> None:
        """Extract with the [<task>] model, then build complete datasets. Billed per token.

        Without --spend, print pending units and estimated cost, and send nothing.
        """
        cfg = load_settings(name, registry)
        if task not in tasks.BY_NAME:
            raise fail(f"unknown task {task!r}; known: {', '.join(tasks.BY_NAME)}")
        chosen = tasks.BY_NAME[task]
        http: httpx.Client | None = None
        try:
            settings = task_mod.settings(chosen, cfg.table(task), registry.chat_models)
            wanted = jurisdiction or jurisdictions(cfg)
            complete = None
            if spend > 0:
                http = make_client(settings.model.provider)
                schema = chosen.output.model_json_schema()
                complete = partial(_complete, http, settings.model, schema)
            left = spend
            for j in wanted:
                found = rows.load(cfg, j)
                if found is None:
                    typer.echo(f"{task:14} {j:28} no derived law", err=True)
                    continue
                result = run.run(
                    cfg, chosen, settings, found, complete,
                    spend=left, writer=getpass.getuser(),
                )  # fmt: skip
                left -= result.spent
                typer.echo(result.summary)
            if spend > 0:
                store = Store(cfg)
                for b in build.build(
                    cfg, store, registry.chat_models, registry.citations, wanted, task=task
                ):
                    typer.echo(b.summary)
                catalog.build(cfg, catalog_views(cfg, registry))
        except NomoscopeError as e:
            raise fail(str(e)) from e
        finally:
            if http is not None:
                http.close()
        if spend > 0:
            typer.echo(f"spent about ${spend - left:.4f}", err=True)


def _complete(
    http: httpx.Client,
    model: chat.ChatModel,
    schema: dict[str, Any],
    messages: list[dict[str, str]],
) -> chat.Completion:
    return chat.complete(http, model, messages, schema)
