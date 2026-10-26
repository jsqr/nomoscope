"""A cruncher that parses each record with its code's grammar.

Each record has two steps. Parse reads the record, parses it, runs the
checks, and writes the snapshot tables `parse_lines`, `parse_nodes`,
and `parse_findings`. The grammar file is one of its inputs. Carve
writes the carved tables and records whether derive may read them
(`steps.admitted`): only once the grammar is reviewed and no check has
more findings than the review accepted.

A grammar is at `grammars/<source>/<jurisdiction>/<code>.toml` in the
config root.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from nomoscope import structure
from nomoscope.crunch import CrunchError, runner, schemas
from nomoscope.crunch import routes as routing
from nomoscope.crunch.layout import Layout
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.results import CrunchResult
from nomoscope.crunch.runner import record_plan, write_carved, write_snapshot
from nomoscope.graph import fingerprint
from nomoscope.graph import records as graph_records
from nomoscope.graph.records import RecordRow
from nomoscope.graph.steps import CARVE, CONFIG, CRUNCH, Input, Planned, StepKey
from nomoscope.graph.store import Store
from nomoscope.settings import Settings
from nomoscope.snarf import archive
from nomoscope.structure import Row, checks, parse, sections
from nomoscope.structure import grammar as gr
from nomoscope.structure import lines as linelib

GRAMMARS = "grammars"
MISSING = "missing"
PARSE_LINES = "parse_lines"
PARSE_NODES = "parse_nodes"
PARSE_FINDINGS = "parse_findings"

LINES_SCHEMA = pl.Schema({"index": pl.Int64, **linelib.SCHEMA})
_LEVELED = pl.List(pl.Struct({"level": pl.String, "number": pl.String}))
NODES_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "parent_id": pl.String,
        "origin": pl.String,
        "bookmark": pl.Int64,
        "level": pl.String,
        "kind": pl.String,
        "style": pl.String,
        "number": pl.String,
        "ordinal": pl.Int64,
        "sub": pl.Int64,
        "label": pl.String,
        "heading": pl.String,
        "depth": pl.Int64,
        "position": pl.Int64,
        "page_first": pl.Int64,
        "page_last": pl.Int64,
        "line_first": pl.Int64,
        "line_last": pl.Int64,
        "text": pl.String,
        "notes": pl.List(pl.String),
        "toc": _LEVELED,
        "refs": pl.List(pl.Struct({"level": pl.String, "value": pl.String})),
        "pieces": pl.List(pl.Struct({"line": pl.Int64, "start": pl.Int64})),
    }
)
FINDINGS_SCHEMA = pl.Schema(checks.SCHEMA)
SNAPSHOTS = {
    PARSE_LINES: LINES_SCHEMA,
    PARSE_NODES: NODES_SCHEMA,
    PARSE_FINDINGS: FINDINGS_SCHEMA,
}


@dataclass(frozen=True)
class Document:
    """A record as lines in reading order, with its outline rows.

    `pages` is the page count, for formats with pages.
    """

    lines: list[Row]
    marks: list[Row] = field(default_factory=list)
    pages: int | None = None


Reader = Callable[[Settings, archive.Record], Document]
StatedOf = Callable[[Settings, RecordRow, archive.Record], schemas.Stated]


def grammar_path(cfg: Settings, row: RecordRow) -> Path | None:
    if cfg.config_root is None:
        return None
    return cfg.config_root / GRAMMARS / row.source / row.jurisdiction / f"{row.code}.toml"


def grammar_input(cfg: Settings, row: RecordRow) -> Input:
    path = grammar_path(cfg, row)
    unit = f"{row.source}/{row.jurisdiction}/{row.code}"
    found = fingerprint.file_sha256(path) if path is not None and path.is_file() else MISSING
    return Input(CONFIG, GRAMMARS, unit, found)


def load_grammar(cfg: Settings, row: RecordRow) -> gr.Grammar:
    path = grammar_path(cfg, row)
    if path is None or not path.is_file():
        where = path or f"<config_root>/{GRAMMARS}/{row.source}/{row.jurisdiction}/{row.code}.toml"
        raise CrunchError(
            f"{row.record}: no grammar at {where}; draft one from `grammar sample "
            f"{row.source} {row.record}`"
        )
    return gr.load(path)


def nothing_stated(cfg: Settings, row: RecordRow, record: archive.Record) -> schemas.Stated:
    return schemas.NOTHING_STATED


def frame(rows: list[Row], schema: pl.Schema) -> pl.DataFrame:
    """Build a frame of `schema` from rows, with missing columns null."""
    if not rows:
        return pl.DataFrame(schema=schema)
    return pl.DataFrame(
        [{name: r.get(name) for name in schema.names()} for r in rows], schema=schema
    )


def parsed(grammar: gr.Grammar, doc: Document) -> tuple[list[Row], list[Row]]:
    """Parse a document and run the checks."""
    nodes = parse.parse(doc.lines, grammar, doc.marks)
    found = checks.run(nodes, pl.DataFrame(doc.marks), grammar, doc.lines)
    if doc.pages is not None:
        found += checks.textless(doc.lines, doc.pages)
    return nodes, found


def admission(grammar: gr.Grammar, counts: Mapping[str, int]) -> dict[str, object]:
    worse = checks.worse(dict(counts), grammar.review.findings)
    return {
        "admitted": grammar.review.reviewed and not worse,
        "review": grammar.review.status,
        "findings": dict(counts),
        "worse": worse,
    }


@dataclass(frozen=True)
class Checked:
    """A parse summarized for review: node counts, sections, findings, and admission."""

    levels: dict[str, int]
    sections: list[Row]
    findings: list[Row]
    admission: dict[str, object]
    outline: pl.DataFrame | None


def check(grammar: gr.Grammar, doc: Document) -> Checked:
    nodes, found = parsed(grammar, doc)
    levels = Counter(n["level"] for n in nodes if n["kind"] != "root")
    carved = sections.carve(nodes, doc.lines, grammar)
    marks = pl.DataFrame(doc.marks)
    outline = checks.outline_summary(nodes, marks, grammar) if doc.marks else None
    return Checked(
        dict(levels), carved.sections, found, admission(grammar, checks.counts(found)), outline
    )


@dataclass(frozen=True)
class GrammarCruncher:
    """Parse each record with its grammar, then carve. Bump `version` when output changes."""

    read: Reader
    version: str
    stated: StatedOf = nothing_stated
    zone: str = "UTC"

    def plan(self, cfg: Settings, name: str, rows: Sequence[RecordRow]) -> list[Planned]:
        version = f"{self.version}-{structure.VERSION}"
        parse_version = fingerprint.code_version(f"{version}-parse", *SNAPSHOTS.values())
        carve_version = schemas.carve_version(version, self.zone)
        planned = []
        for p in record_plan(name, rows, parse_version, extra=lambda r: (grammar_input(cfg, r),)):
            planned.append(p)
            planned.append(Planned(StepKey(CARVE, name, p.key.unit), (p.input,), carve_version))
        return planned

    def run(
        self,
        cfg: Settings,
        store: Store,
        planned: Planned,
        rows: Mapping[str, RecordRow],
    ) -> CrunchResult:
        row = rows[planned.key.unit]
        if planned.key.stage == CRUNCH:
            return self.parse(cfg, store, planned, row)
        return self.carve(cfg, store, planned, row)

    def parse(self, cfg: Settings, store: Store, planned: Planned, row: RecordRow) -> CrunchResult:
        record = archive.find(cfg.snarfed, row.source, row.record)
        doc = self.read(cfg, record)
        nodes, found = parsed(load_grammar(cfg, row), doc)
        indexed = [{"index": i, **line} for i, line in enumerate(doc.lines)]
        built = {
            PARSE_LINES: frame(indexed, LINES_SCHEMA),
            PARSE_NODES: frame(nodes, NODES_SCHEMA),
            PARSE_FINDINGS: frame(found, FINDINGS_SCHEMA),
        }
        details = {"findings": checks.counts(found), "nodes": len(nodes)}
        tables = write_snapshot(cfg, store, planned, built, details)
        return CrunchResult(planned.key.scope, CRUNCH, row.record, row.jurisdiction, tables)

    def carve(self, cfg: Settings, store: Store, planned: Planned, row: RecordRow) -> CrunchResult:
        lay = Layout(planned.key.scope)
        read = {
            name: pl.read_parquet(lay.snapshot_table(cfg, name, row.record)) for name in SNAPSHOTS
        }
        grammar = load_grammar(cfg, row)
        lines = read[PARSE_LINES].sort("index").to_dicts()
        carved = sections.carve(read[PARSE_NODES].to_dicts(), lines, grammar)
        blob = lines[0]["sha256"] if lines else None
        common = {"jurisdiction": row.jurisdiction, "law_code": row.code}
        record = archive.find(cfg.snarfed, row.source, row.record)
        built = {
            schemas.LAW_SECTIONS: section_frame(carved.sections, common, blob),
            schemas.LAW_NODES: node_frame(carved.nodes, common),
            schemas.LAW_PUBLICATIONS: schemas.publication(
                row, [row.code], self.stated(cfg, row, record)
            ),
        }
        counts = checks.counts(read[PARSE_FINDINGS].to_dicts())
        details = admission(grammar, counts)
        tables = write_carved(cfg, store, planned, built, self.zone, details)
        return CrunchResult(planned.key.scope, CARVE, row.record, row.jurisdiction, tables)


SECTION_COLUMNS = ("section_num", "node_treepath", "heading", "history", "text", "drafting")
NODE_COLUMNS = (
    "node_treepath",
    "parent_treepath",
    "level",
    "number",
    "heading",
    "depth",
    "position",
    "sequence",
    "contains_sections",
)


def section_frame(found: list[Row], common: Mapping[str, str], blob: str | None) -> pl.DataFrame:
    schema = schemas.carved("UTC")[schemas.LAW_SECTIONS]
    cols = ("jurisdiction", "law_code", *SECTION_COLUMNS, "blob_sha256")
    rows = [{**common, **{c: s[c] for c in SECTION_COLUMNS}, "blob_sha256": blob} for s in found]
    return pl.DataFrame(rows, schema={c: schema[c] for c in cols})


def node_frame(found: list[Row], common: Mapping[str, str]) -> pl.DataFrame:
    schema = schemas.carved("UTC")[schemas.LAW_NODES]
    cols = ("jurisdiction", "law_code", *NODE_COLUMNS)
    rows = [{**common, **{c: n[c] for c in NODE_COLUMNS}} for n in found]
    return pl.DataFrame(rows, schema={c: schema[c] for c in cols})


def resolve(
    cfg: Settings, store: Store, registry: Registry, source: str, record_id: str
) -> tuple[GrammarCruncher, RecordRow, archive.Record]:
    """Find a record, its registration, and the grammar cruncher it routes to."""
    runner.register(cfg, store, registry)
    with store.open(read_only=True) as conn:
        found = [
            r for r in graph_records.records(conn) if r.source == source and r.record == record_id
        ]
    if not found:
        raise CrunchError(f"no registered record {record_id} of source {source}")
    row = found[0]
    name = routing.route(routing.load(cfg), row)
    if name is None:
        raise CrunchError(f"{record_id}: no route in {routing.FILE}")
    cruncher = registry.cruncher(name)
    if not isinstance(cruncher, GrammarCruncher):
        raise CrunchError(f"{record_id}: cruncher {name} does not parse with a grammar")
    return cruncher, row, archive.find(cfg.snarfed, source, record_id)
