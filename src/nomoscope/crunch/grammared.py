"""A cruncher that parses each record with its code's grammar.

Each record has two steps. Parse reads the record, parses it, runs the
checks, and writes the snapshot tables `parse_lines`, `parse_nodes`,
and `parse_findings`. The grammar file is one of its inputs. Tabulate
writes the tabulated tables and records whether derive may read them
(`steps.admitted`): only once a person has accepted a record of the code
(`reviews`), no check has more findings than they accepted, and, for the
record they accepted, no section has changed since. Other records of the
code are judged on their findings.

A grammar is at `<source>/<jurisdiction>/<code>.toml` in the cruncher's
grammars directory, which the project gives when it creates the cruncher.
"""

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import polars as pl

from nomoscope import structure
from nomoscope.crunch import CrunchError, reviews, runner, schemas
from nomoscope.crunch import routes as routing
from nomoscope.crunch.layout import Layout
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.results import CrunchResult
from nomoscope.crunch.runner import record_plan, write_snapshot, write_tabulated
from nomoscope.graph import fingerprint
from nomoscope.graph import records as graph_records
from nomoscope.graph.records import RECORD, RecordRow
from nomoscope.graph.steps import CONFIG, CRUNCH, TABULATE, Input, Planned, StepKey
from nomoscope.graph.store import Store
from nomoscope.settings import Settings
from nomoscope.snarf import archive, attach
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


def grammar_path(grammars: Path | None, row: RecordRow) -> Path | None:
    if grammars is None:
        return None
    return grammars / row.source / row.jurisdiction / f"{row.code}.toml"


def grammar_input(grammars: Path | None, row: RecordRow) -> Input:
    path = grammar_path(grammars, row)
    unit = f"{row.source}/{row.jurisdiction}/{row.code}"
    found = fingerprint.file_sha256(path) if path is not None and path.is_file() else MISSING
    return Input(CONFIG, GRAMMARS, unit, found)


def review_input(cfg: Settings, row: RecordRow) -> Input:
    """The code's review in force, as an input of the tabulate step."""
    found = reviews.latest(cfg, row.source, row.jurisdiction, row.code)
    unit = f"{row.source}/{row.jurisdiction}/{row.code}"
    # Like a config file, a review is outside the store, which keeps no hash of it.
    return Input(CONFIG, reviews.REVIEWS, unit, found.review_id if found else MISSING)


def in_force(cfg: Settings, row: RecordRow) -> tuple[reviews.Review | None, pl.DataFrame | None]:
    """The code's review in force and the sections it accepted, if it has one."""
    found = reviews.latest(cfg, row.source, row.jurisdiction, row.code)
    return found, reviews.accepted_sections(cfg, found) if found else None


def since_review(
    review: reviews.Review | None, accepted: pl.DataFrame | None, record: str, tabulated: list[Row]
) -> list[Row] | None:
    """Sections changed since the review; none for a record other than the reviewed one.

    None when the code has no review.
    """
    if review is None or accepted is None:
        return None
    if record != review.record:
        return []
    return reviews.changes(accepted, reviews.summary(tabulated))


def load_grammar(grammars: Path | None, row: RecordRow) -> gr.Grammar:
    path = grammar_path(grammars, row)
    if path is None or not path.is_file():
        where = path or f"<grammars>/{row.source}/{row.jurisdiction}/{row.code}.toml"
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


def admission(
    review: reviews.Review | None, counts: Mapping[str, int], changes: list[Row] | None
) -> dict[str, object]:
    """Whether derive may read a tabulation."""
    worse = checks.worse(dict(counts), review.findings) if review else {}
    return {
        "admitted": review is not None and not worse and not changes,
        "review": review.review_id if review else None,
        "findings": dict(counts),
        "worse": worse,
        "changed": len(changes or []),
    }


def held(a: Mapping[str, object]) -> str:
    """Why derive leaves a tabulation out, or "admitted"."""
    if a["admitted"]:
        return "admitted"
    reasons = []
    if a["review"] is None:
        reasons.append("not accepted; run grammar accept")
    if a["worse"]:
        reasons.append(f"worse {a['worse']}")
    if a["changed"]:
        reasons.append(f"{a['changed']} sections changed since review")
    return f"held ({'; '.join(reasons)})"


@dataclass(frozen=True)
class Checked:
    """A parse summarized for review: node counts, sections, findings, and admission."""

    levels: dict[str, int]
    sections: list[Row]
    findings: list[Row]
    admission: dict[str, object]
    outline: pl.DataFrame | None
    changes: list[Row] | None


def check(
    grammar: gr.Grammar,
    doc: Document,
    review: reviews.Review | None,
    accepted: pl.DataFrame | None,
    record: str,
) -> Checked:
    nodes, found = parsed(grammar, doc)
    levels = Counter(n["level"] for n in nodes if n["kind"] != "root")
    tabulated = sections.tabulate(nodes, doc.lines, grammar)
    marks = pl.DataFrame(doc.marks)
    outline = checks.outline_summary(nodes, marks, grammar) if doc.marks else None
    changes = since_review(review, accepted, record, tabulated.sections)
    a = admission(review, checks.counts(found), changes)
    return Checked(dict(levels), tabulated.sections, found, a, outline, changes)


@dataclass(frozen=True)
class GrammarCruncher:
    """Parse each record with its grammar, then tabulate. Bump `version` when output changes."""

    read: Reader
    version: str
    grammars: Path | None = None
    stated: StatedOf = nothing_stated
    zone: str = "UTC"

    def plan(self, cfg: Settings, name: str, rows: Sequence[RecordRow]) -> list[Planned]:
        version = f"{self.version}-{structure.VERSION}"
        parse_version = fingerprint.code_version(f"{version}-parse", *SNAPSHOTS.values())
        tabulate_version = schemas.tabulate_version(version, self.zone)
        attached = {source: attach.index(cfg, source) for source in {r.source for r in rows}}

        def inputs(r: RecordRow) -> tuple[Input, ...]:
            # Attached records, such as OCR responses, are read with the record.
            extra = attached[r.source].get(r.record, [])
            found = (Input(RECORD, r.source, a.id, a.fetch().sha256) for a in extra)
            return (grammar_input(self.grammars, r), *found)

        by_record = {r.record: r for r in rows}
        planned = []
        for p in record_plan(name, rows, parse_version, extra=inputs):
            planned.append(p)
            row = by_record[p.key.unit]
            tabulate_inputs = (p.input, review_input(cfg, row))
            planned.append(
                Planned(StepKey(TABULATE, name, p.key.unit), tabulate_inputs, tabulate_version)
            )
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
        return self.tabulate(cfg, store, planned, row)

    def parse(self, cfg: Settings, store: Store, planned: Planned, row: RecordRow) -> CrunchResult:
        record = archive.find(cfg.snarfed, row.source, row.record)
        doc = self.read(cfg, record)
        nodes, found = parsed(load_grammar(self.grammars, row), doc)
        indexed = [{"index": i, **line} for i, line in enumerate(doc.lines)]
        built = {
            PARSE_LINES: frame(indexed, LINES_SCHEMA),
            PARSE_NODES: frame(nodes, NODES_SCHEMA),
            PARSE_FINDINGS: frame(found, FINDINGS_SCHEMA),
        }
        details = {"findings": checks.counts(found), "nodes": len(nodes)}
        tables = write_snapshot(cfg, store, planned, built, details)
        return CrunchResult(planned.key.scope, CRUNCH, row.record, row.jurisdiction, tables)

    def tabulate(
        self, cfg: Settings, store: Store, planned: Planned, row: RecordRow
    ) -> CrunchResult:
        lay = Layout(planned.key.scope)
        read = {
            name: pl.read_parquet(lay.snapshot_table(cfg, name, row.record)) for name in SNAPSHOTS
        }
        grammar = load_grammar(self.grammars, row)
        lines = read[PARSE_LINES].sort("index").to_dicts()
        tabulated = sections.tabulate(read[PARSE_NODES].to_dicts(), lines, grammar)
        blob = lines[0]["sha256"] if lines else None
        common = {"jurisdiction": row.jurisdiction, "law_code": row.code}
        record = archive.find(cfg.snarfed, row.source, row.record)
        built = {
            schemas.LAW_SECTIONS: section_frame(tabulated.sections, common, blob),
            schemas.LAW_NODES: node_frame(tabulated.nodes, common),
            schemas.LAW_PUBLICATIONS: schemas.publication(
                row, [row.code], self.stated(cfg, row, record)
            ),
        }
        counts = checks.counts(read[PARSE_FINDINGS].to_dicts())
        review, accepted = in_force(cfg, row)
        changes = since_review(review, accepted, row.record, tabulated.sections)
        details = admission(review, counts, changes)
        tables = write_tabulated(cfg, store, planned, built, self.zone, details)
        return CrunchResult(planned.key.scope, TABULATE, row.record, row.jurisdiction, tables)


SECTION_COLUMNS = (
    "section_num",
    "node_treepath",
    "heading",
    "history",
    "division_history",
    "text",
    "subdivisions",
    "drafting",
)
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
    schema = schemas.tabulated("UTC")[schemas.LAW_SECTIONS]
    cols = ("jurisdiction", "law_code", *SECTION_COLUMNS, "blob_sha256")
    rows = [{**common, **{c: s[c] for c in SECTION_COLUMNS}, "blob_sha256": blob} for s in found]
    return pl.DataFrame(rows, schema={c: schema[c] for c in cols})


def node_frame(found: list[Row], common: Mapping[str, str]) -> pl.DataFrame:
    schema = schemas.tabulated("UTC")[schemas.LAW_NODES]
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
    name = routing.route(registry.routes, row)
    if name is None:
        raise CrunchError(f"{record_id}: no route for source {source} in the registry")
    cruncher = registry.cruncher(name)
    if not isinstance(cruncher, GrammarCruncher):
        raise CrunchError(f"{record_id}: cruncher {name} does not parse with a grammar")
    return cruncher, row, archive.find(cfg.snarfed, source, record_id)
