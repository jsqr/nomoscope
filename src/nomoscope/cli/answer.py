"""The ask command: answers from retrieved passages, billed per token."""

import getpass
import json
from datetime import date
from functools import partial
from pathlib import Path
from typing import Annotated

import httpx
import typer

from nomoscope import chat
from nomoscope.answer import ask as asking
from nomoscope.answer import group, schema, store
from nomoscope.answer.ask import GroupRun
from nomoscope.catalog import build as catalog
from nomoscope.chat import ChatModel, Completion
from nomoscope.cli.common import catalog_views, fail, load_settings
from nomoscope.crunch.registry import Registry
from nomoscope.errors import NomoscopeError
from nomoscope.providers import make_client
from nomoscope.search import query as searching


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    @app.command()
    def ask(
        question: Annotated[str | None, typer.Argument(help="One question")] = None,
        group_file: Annotated[
            Path | None, typer.Option("--group", help="A TOML file of questions that form a DAG")
        ] = None,
        jurisdiction: Annotated[
            list[str],
            typer.Option("--jurisdiction", "-j", help="Jurisdiction id; repeatable"),
        ] = [],  # noqa: B006
        on: Annotated[
            str | None, typer.Option(help="The date, YYYY-MM-DD; default today, UTC")
        ] = None,
        spend: Annotated[
            float, typer.Option(help="Dollars this run may spend; 0 only estimates")
        ] = 0.0,
        output: Annotated[str, typer.Option("--format", help="table or json")] = "table",
    ) -> None:
        """Answer a question, or a group in dependency order, from the law in force.

        Each answer cites the passages it rests on and gives a confidence. Billed per
        token: without --spend, print the order and an estimate and call nothing.
        """
        cfg = load_settings(name, registry)
        if (question is None) == (group_file is None):
            raise fail("give a question or --group FILE, not both")
        if not jurisdiction:
            raise fail("give at least one --jurisdiction")
        if output not in ("table", "json"):
            raise fail(f"--format must be table or json, not {output!r}")
        try:
            when = date.fromisoformat(on) if on else date.today()
        except ValueError as e:
            raise fail(f"--on: {e}") from e
        try:
            chosen = asking.options(cfg.table("answers"))
            model = chat.lookup(registry.chat_models, chosen.model, "[answers] model")
            g = group.load(group_file) if group_file else group.single(question or "")
        except NomoscopeError as e:
            raise fail(str(e)) from e
        order = g.order()
        each = asking.estimate(model, chosen.passages, len(order))
        if spend <= 0:
            typer.echo(f"{g.name}: {' -> '.join(q.id for q in order)}")
            typer.echo(
                f"{len(order)} questions x {len(jurisdiction)} jurisdictions,"
                f" about ${each * len(jurisdiction):.3f} with {model.slug}; pass --spend to ask"
            )
            return
        left = spend
        http: httpx.Client | None = None
        try:
            http = make_client(model.provider)
            complete = partial(_complete, http, model)
            for j in jurisdiction:
                retrieve = partial(
                    searching.search, cfg, jurisdiction=j, on=when,
                    text=chosen.text, rerank=chosen.rerank, limit=chosen.passages,
                )  # fmt: skip
                run = asking.ask_group(
                    g, j, when, model=model, passages=chosen.passages,
                    retrieve=retrieve, complete=complete, spend=left,
                )  # fmt: skip
                left -= run.dollars
                store.write(cfg, g, run, getpass.getuser())
                show(run, output)
                if run.unasked:
                    typer.echo(
                        f"{j}: the spend ran out before {', '.join(q.id for q in run.unasked)}",
                        err=True,
                    )
                    break
            if cfg.catalog.is_file():
                catalog.build(cfg, catalog_views(cfg, registry))
        except NomoscopeError as e:
            raise fail(str(e)) from e
        finally:
            if http is not None:
                http.close()
        typer.echo(f"spent about ${spend - left:.4f}", err=True)


def _complete(http: httpx.Client, model: ChatModel, messages: list[dict[str, str]]) -> Completion:
    return chat.complete(http, model, messages, schema.json_schema())


def show(run: GroupRun, output: str) -> None:
    for a in run.answered:
        if output == "json":
            r = a.response
            typer.echo(
                json.dumps(
                    {
                        "jurisdiction": a.jurisdiction,
                        "question_id": a.question.id,
                        "question": a.question.text,
                        "answer": r.answer if r else None,
                        "rationale": r.rationale if r else None,
                        "confidence": r.confidence if r else None,
                        "citations": [
                            {"label": p.label, "citation": p.hit.citation,
                             "law_version_id": p.hit.law_version_id}
                            for p in a.cited
                        ],
                        "error": a.error,
                    }
                )
            )  # fmt: skip
            continue
        typer.echo(f"{a.jurisdiction}  {a.question.id}  {a.question.text}")
        if a.response is None:
            typer.echo(f"    error: {a.error}")
            continue
        typer.echo(f"    {a.response.answer}  (confidence {a.response.confidence:.2f})")
        typer.echo(f"    {a.response.rationale}")
        cited = ", ".join(f"[{p.label}] {p.hit.citation}" for p in a.cited)
        typer.echo(f"    cited: {cited or 'none'}")
