"""The ocr commands: OCR a record's PDF, or attach responses made elsewhere.

`ocr run` makes billed requests. Each writes one attached record (feed
`ocr`) of the record, with one JSON file per segment of pages. With
`--batch`, the segments go in one batch job. The job's id is kept in the
staging directory, so a later `ocr run --batch` of the record waits for
the same job instead of submitting another.
"""

import getpass
import json
import os
import time
from pathlib import Path
from typing import Annotated, Any

import typer
from pypdf import PdfReader

from nomoscope.cli.common import fail, load_settings
from nomoscope.crunch.registry import Registry
from nomoscope.errors import NomoscopeError
from nomoscope.pdf import ocr
from nomoscope.settings import Settings
from nomoscope.snarf import archive, attach
from nomoscope.snarf.run import blob_store

FEED = "ocr"
KEY_ENV = "MISTRAL_API_KEY"
JOB_FILE = "batch.json"
POLL_SECONDS = 60.0

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


def run_batch(
    client: Any,
    reader: PdfReader,
    item: archive.Item,
    todo: list[ocr.Segment],
    staged: Path,
    model: str,
    *,
    wait: bool,
) -> list[str] | None:
    """Stage the responses of a batch job for `todo`.

    Returns None while the job runs, else the segments still not staged.
    """
    job_file = staged / JOB_FILE
    if job_file.is_file():
        job_id = json.loads(job_file.read_text())["job"]
    else:
        lines = (ocr.batch_line(seg.name, ocr.extract(reader, seg)) for seg in todo)
        files = ocr.batch_files(lines)
        job_id = ocr.submit(client, files, model, {"pdf_sha256": item.sha256})
        job_file.write_text(json.dumps({"job": job_id, "model": model}))
        typer.echo(
            f"  submitted job {job_id}: {len(todo)} requests in {len(files)} files", err=True
        )
    while True:
        state = ocr.job(client, job_id)
        typer.echo(
            f"  job {job_id}: {state.status}, {state.completed_requests} of "
            f"{state.total_requests} done",
            err=True,
        )
        if not ocr.running(state.status):
            break
        if not wait:
            return None
        time.sleep(POLL_SECONDS)
    responses, errors = ocr.results(client, state.output_file) if state.output_file else ({}, {})
    by_name = {seg.name: seg for seg in todo}
    for name, response in responses.items():
        (staged / name).write_bytes(ocr.record(response, item.name, item.sha256, by_name[name]))
    for name, error in errors.items():
        typer.echo(f"  {name}: {error}", err=True)
    job_file.unlink()
    missing = [seg.name for seg in todo if not (staged / seg.name).is_file()]
    if missing:
        typer.echo(
            f"  job {job_id} ended {state.status} with {len(missing)} segments missing; "
            "run again to submit them",
            err=True,
        )
    return missing


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
        batch: Annotated[bool, typer.Option(help="Send the requests as one batch job")] = False,
        wait: Annotated[
            bool, typer.Option(help="With --batch, wait for the job; else submit or check it")
        ] = True,
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
        todo = [seg for seg in segs if not (staged / seg.name).is_file()]
        if batch and todo:
            missing = run_batch(client, reader, item, todo, staged, model, wait=wait)
            if missing is None:
                return
            if missing:
                raise typer.Exit(1)
        elif todo:
            for seg in todo:
                response = ocr.process(client, ocr.extract(reader, seg), model)
                (staged / seg.name).write_bytes(ocr.record(response, item.name, item.sha256, seg))
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
