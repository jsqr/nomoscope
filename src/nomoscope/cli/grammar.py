"""The grammar commands: sample, infer, check, and review.

`grammar infer` makes a billed request.
"""

import os
from pathlib import Path
from typing import Annotated

import typer

from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch import grammared
from nomoscope.crunch.registry import Registry
from nomoscope.errors import NomoscopeError
from nomoscope.graph.store import Store
from nomoscope.snarf.run import blob_store
from nomoscope.structure import checks, review, sample
from nomoscope.structure import grammar as gr
from nomoscope.structure import lines as linelib

Source = Annotated[str, typer.Argument(help="Source id")]
RecordId = Annotated[str, typer.Argument(help="Record id")]
REVIEW = "review"


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    group = typer.Typer(no_args_is_help=True, help="Draft and check a code's grammar.")
    app.add_typer(group, name="grammar")

    @group.command("sample")
    def sample_(
        source: Source,
        record: RecordId,
        pages: Annotated[int, typer.Option(help="Pages to show")] = sample.PAGES,
        seed: Annotated[int, typer.Option(help="Seed for the random pages")] = 0,
        instructions: Annotated[
            bool, typer.Option(help="Print what a grammar must give, first")
        ] = False,
    ) -> None:
        """Print a sample of a record's lines and its outline, to draft its grammar from."""
        cfg = load_settings(name)
        try:
            cruncher, row, found = grammared.resolve(cfg, Store(cfg), registry, source, record)
            doc = cruncher.read(cfg, found)
        except NomoscopeError as e:
            raise fail(str(e)) from e
        lines = grammared.frame(doc.lines, linelib.SCHEMA)
        if lines.height == 0:
            raise fail(f"{record}: no lines; the document has no text layer")
        text = "\n\n".join(
            [
                *([sample.INSTRUCTIONS] if instructions else []),
                sample.render_marks(doc.marks),
                sample.render(lines, sample.pages(lines, pages, seed)),
            ]
        )
        typer.echo(text)
        typer.echo(
            f"{len(doc.lines)} lines; sample of about {sample.tokens(text)} tokens."
            f" Grammar goes to {grammared.grammar_path(cfg, row)}",
            err=True,
        )

    @group.command("check")
    def check(
        source: Source,
        record: RecordId,
        grammar: Annotated[
            Path | None, typer.Option(help="Grammar file; default the record's own")
        ] = None,
        limit: Annotated[int, typer.Option(help="Findings to show per check")] = 5,
    ) -> None:
        """Parse a record with a grammar and report what looks wrong. Writes nothing."""
        cfg = load_settings(name)
        try:
            cruncher, row, found = grammared.resolve(cfg, Store(cfg), registry, source, record)
            g = gr.load(grammar) if grammar else grammared.load_grammar(cfg, row)
            checked = grammared.check(g, cruncher.read(cfg, found))
        except (NomoscopeError, ValueError) as e:
            raise fail(str(e)) from e
        report(checked, limit)

    @group.command("review")
    def review_(
        source: Source,
        record: RecordId,
        grammar: Annotated[
            Path | None, typer.Option(help="Grammar file; default the record's own")
        ] = None,
        out: Annotated[
            Path | None, typer.Option(help="Directory; default review/<source>/<record>/")
        ] = None,
    ) -> None:
        """Write the record's parse as an HTML page, beside a link to its source file."""
        cfg = load_settings(name)
        try:
            cruncher, row, found = grammared.resolve(cfg, Store(cfg), registry, source, record)
            path = grammar or grammared.grammar_path(cfg, row)
            g = gr.load(grammar) if grammar else grammared.load_grammar(cfg, row)
            doc = cruncher.read(cfg, found)
        except (NomoscopeError, ValueError) as e:
            raise fail(str(e)) from e
        nodes, findings = grammared.parsed(g, doc)
        a = grammared.admission(g, checks.counts(findings))
        target = out or cfg.data_root / REVIEW / source / record
        target.mkdir(parents=True, exist_ok=True)
        href = None
        first = doc.lines[0] if doc.lines else {}
        if first.get("source") and first.get("sha256"):
            href = Path(first["source"]).name
            link = target / href
            link.unlink(missing_ok=True)
            link.symlink_to(blob_store(cfg).path(first["sha256"]))
        held = "admitted" if a["admitted"] else f"held (worse {a['worse']})"
        header = [
            ("record", record),
            ("source", source),
            ("jurisdiction", row.jurisdiction),
            ("code", row.code),
            ("grammar", str(path)),
            ("review", str(a["review"])),
            ("findings", str(a["findings"] or "none")),
            ("derive", held),
        ]
        page = review.render(nodes, doc.lines, g, findings, header, href if doc.pages else None)
        (target / "index.html").write_text(page, encoding="utf-8")
        typer.echo(target / "index.html")

    @group.command("infer")
    def infer_(
        source: Source,
        record: RecordId,
        out: Annotated[
            Path | None, typer.Option(help="Where to write; default the record's grammar path")
        ] = None,
        pages: Annotated[int, typer.Option(help="Pages to show the model")] = sample.PAGES,
        seed: Annotated[int, typer.Option(help="Seed for the random pages")] = 0,
        effort: Annotated[str | None, typer.Option(help="Reasoning effort to send")] = None,
        max_tokens: Annotated[int | None, typer.Option(help="Output token budget")] = None,
        dry_run: Annotated[bool, typer.Option(help="Show the sample's size only")] = False,
        force: Annotated[bool, typer.Option(help="Overwrite an existing grammar")] = False,
    ) -> None:
        """Draft a record's grammar with a chat model. Billed per token."""
        cfg = load_settings(name)
        try:
            cruncher, row, found = grammared.resolve(cfg, Store(cfg), registry, source, record)
            doc = cruncher.read(cfg, found)
        except NomoscopeError as e:
            raise fail(str(e)) from e
        lines = grammared.frame(doc.lines, linelib.SCHEMA)
        if lines.height == 0:
            raise fail(f"{record}: no lines; the document has no text layer")
        chosen = sample.pages(lines, pages, seed)
        size = sample.tokens(sample.render(lines, chosen))
        typer.echo(
            f"{len(chosen)} pages, about {size} input tokens: {' '.join(map(str, chosen))}",
            err=True,
        )
        if dry_run:
            return
        model_name = os.environ.get(model_env(name))
        if not model_name:
            raise fail(f"set {model_env(name)} to the model id")
        target = out or grammared.grammar_path(cfg, row)
        if target is None:
            raise fail("no config_root; pass --out")
        if target.exists() and not force:
            raise fail(f"{target} exists; pass --out or --force")
        try:
            from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior

            from nomoscope.structure import infer
        except ImportError as e:
            raise fail(f"grammar infer needs the infer extra: {e}") from e
        key = os.environ.get(infer.KEY_ENV)
        if not key:
            raise fail(f"set {infer.KEY_ENV}")
        try:
            model = infer.backend(model_name, key, effort=effort or infer.EFFORT)
            inferred = infer.infer(lines, model, model_name, chosen, max_tokens or infer.MAX_TOKENS)
        except ValueError as e:
            raise fail(str(e)) from e
        except ModelHTTPError as e:
            raise fail(f"{model_name}: HTTP {e.status_code}: {e.body}") from e
        except UnexpectedModelBehavior as e:
            raise fail(f"{model_name}: {e.message}") from e
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(gr.dumps(inferred.grammar, infer.comment(inferred)), encoding="utf-8")
        typer.echo(
            f"{target}: {inferred.input_tokens} tokens in, {inferred.output_tokens} out", err=True
        )


def model_env(name: str) -> str:
    return f"{name.upper()}_GRAMMAR_MODEL"


def report(checked: grammared.Checked, limit: int) -> None:
    levels = ", ".join(f"{level} {n}" for level, n in checked.levels.items())
    typer.echo(f"nodes:     {levels}")
    typer.echo(f"sections:  {len(checked.sections)}")
    for s in checked.sections[:3]:
        history = " | history" if s["history"] else ""
        typer.echo(f"  {s['section_num']:12} {s['heading'] or ''}{history}")
    if checked.outline is not None:
        typer.echo("outline:")
        for o in checked.outline.sort("level").iter_rows(named=True):
            typer.echo(
                f"  {o['level']:12} bookmarks {o['bookmarks'] or 0:5}"
                f"  nodes {o['nodes'] or 0:5}  recall {o['recall']}  precision {o['precision']}"
            )
    a = checked.admission
    typer.echo(f"findings:  {a['findings'] or 'none'}")
    by_check: dict[str, list[str]] = {}
    for f in checked.findings:
        by_check.setdefault(f["check"], []).append(f"{f['node_id'] or '-'}: {f['detail']}")
    for name, shown in by_check.items():
        for line in shown[:limit]:
            typer.echo(f"  {name:10} {line}")
        if len(shown) > limit:
            typer.echo(f"  {name:10} ... {len(shown) - limit} more")
    held = "admitted" if a["admitted"] else f"held (review {a['review']}, worse {a['worse']})"
    typer.echo(f"derive:    {held}")
