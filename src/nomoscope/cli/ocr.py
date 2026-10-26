"""The ocr commands: OCR a record's PDF, or attach responses made elsewhere.

`ocr run` makes billed requests. Each writes one attached record (feed
`ocr`) of the record, with one JSON file per segment of pages.
"""

import getpass
import json
import os
from pathlib import Path
from typing import Annotated

import typer
from pypdf import PdfReader

from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch.registry import Registry
from nomoscope.errors import NomoscopeError
from nomoscope.settings import Settings
from nomoscope.snarf import archive, attach
from nomoscope.snarf.run import blob_store
from nomoscope.structure import ocr

FEED = "ocr"
KEY_ENV = "MISTRAL_API_KEY"

Source = Annotated[str, typer.Argument(help="Source id")]
RecordId = Annotated[str, typer.Argument(help="Record id")]


def pdf_item(record: archive.Record) -> archive.Item:
    found = [i for i in record.items() if i.name.lower().endswith(".pdf")]
    if len(found) != 1:
        raise NomoscopeError(
            f"{record.id}: want one .pdf file, have {[i.name for i in record.items()]}"
        )
    return found[0]


def params(item: archive.Item, model: str, importer: str | None = None) -> dict[str, str]:
    found = {"pdf": item.name, "pdf_sha256": item.sha256, "model": model}
    return found | ({"importer": importer} if importer else {})


def add(app: typer.Typer, registry: Registry, name: str) -> None:
    group = typer.Typer(no_args_is_help=True, help="OCR a record's PDF; attach the responses.")
    app.add_typer(group, name="ocr")

    def located(source: str, record_id: str) -> tuple[Settings, archive.Record, archive.Item]:
        cfg = load_settings(name)
        try:
            registry.source(source).feed(FEED)
            record = archive.find(cfg.snarfed, source, record_id)
            return cfg, record, pdf_item(record)
        except NomoscopeError as e:
            raise fail(str(e)) from e

    @group.command("run")
    def run_(
        source: Source,
        record: RecordId,
        pages: Annotated[int, typer.Option(help="Pages per request")] = ocr.SEGMENT_PAGES,
        model: Annotated[str, typer.Option(help="OCR model")] = ocr.MODEL,
        dry_run: Annotated[bool, typer.Option(help="Show pages and requests only")] = False,
    ) -> None:
        """OCR the record's PDF. Billed per page."""
        cfg, found, item = located(source, record)
        path = blob_store(cfg).path(item.sha256)
        reader = PdfReader(path)
        segs = ocr.segments(len(reader.pages), pages)
        typer.echo(f"{item.name}: {len(reader.pages)} pages in {len(segs)} requests", err=True)
        if dry_run:
            return
        key = os.environ.get(KEY_ENV)
        if not key:
            raise fail(f"set {KEY_ENV}")
        try:
            from mistralai.client import Mistral
        except ImportError as e:
            raise fail(f"ocr run needs the ocr extra: {e}") from e
        client = Mistral(api_key=key)
        staged = cfg.staging / FEED / item.sha256
        staged.mkdir(parents=True, exist_ok=True)
        for seg in segs:
            out = staged / seg.name
            if out.is_file():
                continue
            response = ocr.process(client, ocr.extract(reader, seg), model)
            out.write_bytes(ocr.record(response, item.name, item.sha256, seg))
            typer.echo(f"  {seg.name}", err=True)
        files = {seg.name: (staged / seg.name).read_bytes() for seg in segs}
        written = attach.write(
            cfg, registry.source(source), found, FEED, files, params(item, model)
        )
        typer.echo(written.id)

    @group.command("import")
    def import_(
        source: Source,
        record: RecordId,
        directory: Annotated[Path, typer.Argument(help="Segment responses, <first>-<last>.json")],
        importer: Annotated[
            str | None, typer.Option(help="Who made the responses; default the current user")
        ] = None,
    ) -> None:
        """Attach OCR responses made elsewhere, for this record's PDF."""
        cfg, found, item = located(source, record)
        files = {p.name: p.read_bytes() for p in sorted(directory.glob("*.json"))}
        if not files:
            raise fail(f"{directory}: no .json files")
        models = set()
        for file_name, data in files.items():
            stored = json.loads(data)
            if stored.get("sha256") != item.sha256:
                raise fail(f"{file_name}: made from another file than {item.name}")
            models.add(stored["response"].get("model", ""))
        who = importer or getpass.getuser()
        model = ",".join(sorted(models))
        written = attach.write(
            cfg, registry.source(source), found, FEED, files, params(item, model, who)
        )
        typer.echo(written.id)
