import json
from dataclasses import replace
from datetime import UTC, timedelta
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from nomoscope import cli
from nomoscope.catalog import build as catalog
from nomoscope.crunch import CrunchError, reviews, runner
from nomoscope.crunch.grammared import Document, GrammarCruncher, grammar_path
from nomoscope.crunch.layout import Layout
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.routes import ANY, Route
from nomoscope.derive import build
from nomoscope.graph import steps
from nomoscope.graph.steps import TABULATE
from nomoscope.graph.store import Store
from nomoscope.partition import dataset_path
from nomoscope.settings import Settings
from nomoscope.snarf import archive, attach, local
from nomoscope.snarf.feeds import Feed, Source
from nomoscope.snarf.run import blob_store
from nomoscope.structure import grammar as gr
from nomoscope.structure import infer
from tests import fake
from tests.structure import GRAMMAR, B, C, lines
from tests.test_structure_infer import ANSWER, completion, server

NAME = "printed"
JURISDICTION = "us-il-windytown"
DOCUMENT = [
    ["Chapter 5", 280, C],
    ["FIRE", 280, C],
    ["§ 5-1. Purpose.", 72, B],
    ["The Town finds that fires happen.", 72],
    ["A. Permits are required.", 72],
    ["Editor's note: See chapter 9.", 72],
    ["(Ord. 3, 2019)", 72],
    ["§ 5-3. Fees.", 72, B],
    ["Fees are set by resolution.", 72],
]
RULES = gr.Sections(history=[r"^\(Ord\. "], drop=[r"^Editor's note"])


def read(cfg: Settings, record: archive.Record) -> Document:
    [item] = record.items()
    rows = json.loads(blob_store(cfg).read(item.sha256))
    found = [{**line, "source": item.name, "sha256": item.sha256} for line in lines(*rows)]
    return Document(found, pages=1)


OCR = Feed("ocr", (), "none", days=(), completeness="full", crunchable=False)
SOURCE = Source(fake.SOURCE_ID, (fake.CODE, OCR), (), lambda d: {})
REGISTRY = Registry(
    sources={fake.SOURCE_ID: SOURCE},
    crunchers={NAME: GrammarCruncher(read, "1")},
    routes=(Route(fake.SOURCE_ID, ANY, ANY, NAME),),
)


@pytest.fixture(autouse=True)
def grammars(tmp_path, monkeypatch) -> Path:
    """Give the cruncher a scratch grammars directory."""
    directory = tmp_path / "grammars"
    cruncher = GrammarCruncher(read, "1", grammars=directory)
    monkeypatch.setitem(globals(), "REGISTRY", replace(REGISTRY, crunchers={NAME: cruncher}))
    return directory


@pytest.fixture
def record(cfg, tmp_path) -> archive.Record:
    path = tmp_path / "code.json"
    path.write_text(json.dumps(DOCUMENT))
    out = local.import_path(
        cfg,
        fake.SOURCE,
        fake.CODE,
        path,
        {"jurisdiction": JURISDICTION, "code": "municipal"},
        importer="test",
    )
    assert out.record is not None
    return out.record


def write_grammar(cfg: Settings, rules: gr.Sections = RULES) -> None:
    row = runner_row(cfg)
    path = grammar_path(cruncher_grammars(), row)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    g = GRAMMAR.model_copy(update={"sections": rules})
    path.write_text(gr.dumps(g))


def accept(record: archive.Record, *extra: str) -> str:
    return invoke("grammar", "accept", fake.SOURCE_ID, record.id, "--by", "t", *extra)


def amend(cfg: Settings, **changes) -> None:
    """Write a later review of the code, like the one in force but for `changes`."""
    row = runner_row(cfg)
    found = reviews.latest(cfg, row.source, row.jurisdiction, row.code)
    assert found is not None
    later = found.reviewed_at.replace(tzinfo=UTC) + timedelta(seconds=1)
    new = replace(found, **changes, reviewed_at=later.replace(tzinfo=None))
    new = replace(new, review_id=reviews.review_id(later, new.record))
    reviews.write(cfg, new, reviews.accepted_sections(cfg, found))


def cruncher_grammars() -> Path | None:
    cruncher = REGISTRY.cruncher(NAME)
    assert isinstance(cruncher, GrammarCruncher)
    return cruncher.grammars


def runner_row(cfg: Settings):
    store = Store(cfg)
    runner.register(cfg, store, REGISTRY)
    with store.open(read_only=True) as conn:
        [routed] = runner.plans(cfg, conn, REGISTRY, REGISTRY.routes)
    [row] = routed.rows.values()
    return row


def crunch(cfg: Settings) -> list:
    return runner.crunch(cfg, Store(cfg), REGISTRY)


def tabulate_details(cfg: Settings, record: archive.Record):
    with Store(cfg).open(read_only=True) as conn:
        return steps.stored(conn, TABULATE, NAME)[record.id].details


def derived(cfg: Settings) -> list[build.BuildResult]:
    return build.build(cfg, Store(cfg), REGISTRY)


def test_a_record_without_a_grammar_names_the_path(cfg, record):
    with pytest.raises(CrunchError, match=f"grammars/fake/{JURISDICTION}/municipal.toml"):
        crunch(cfg)


def test_an_unaccepted_code_tabulates_but_derive_holds(cfg, record):
    write_grammar(cfg)
    results = crunch(cfg)
    assert [(r.stage, r.unit) for r in results] == [("crunch", record.id), ("tabulate", record.id)]
    details = tabulate_details(cfg, record)
    assert details is not None
    assert details["admitted"] is False
    assert details["findings"] == {"sequence": 1}
    assert derived(cfg) == []
    with Store(cfg).open(read_only=True) as conn:
        counts = runner.pending(cfg, conn, REGISTRY, REGISTRY.routes)
    assert counts[NAME]["held"] == 1


def test_review_admits_and_derive_builds(cfg, record):
    write_grammar(cfg)
    crunch(cfg)
    accept(record)
    # Accepting reruns the tabulate step, not the parse.
    assert [r.stage for r in crunch(cfg)] == ["tabulate"]
    assert tabulate_details(cfg, record)["admitted"] is True
    [result] = derived(cfg)
    assert result.jurisdiction == JURISDICTION
    assert result.tables["law_versions"] == 2
    subs = pl.read_parquet(dataset_path(cfg, "law_subdivisions", JURISDICTION))
    versions = pl.read_parquet(dataset_path(cfg, "law_versions", JURISDICTION))
    text = dict(versions.select("id", "text").iter_rows())
    [a] = subs.to_dicts()
    assert (a["path"], a["level"], a["style"], a["number"]) == (
        "A",
        "subsection",
        "upper_alpha",
        "A",
    )
    assert text[a["law_version_id"]][a["start_char"] : a["end_char"]] == "A. Permits are required."


def test_more_findings_than_accepted_hold(cfg, record):
    write_grammar(cfg)
    accept(record)
    amend(cfg, findings={})
    crunch(cfg)
    details = tabulate_details(cfg, record)
    assert details["admitted"] is False
    assert details["worse"] == {"sequence": 1}


def test_sections_carry_text_history_and_drop_notes(cfg, record):
    write_grammar(cfg)
    crunch(cfg)
    path = Layout(NAME).tabulated_table(cfg, "law_sections", record.id)
    rows = pl.read_parquet(path).sort("section_num").to_dicts()
    assert [r["section_num"] for r in rows] == ["5-1", "5-3"]
    assert rows[0]["text"] == "The Town finds that fires happen.\nA. Permits are required."
    assert rows[0]["history"] == "(Ord. 3, 2019)"
    assert rows[0]["node_treepath"] == "5"


def invoke(*args: str) -> str:
    out = CliRunner().invoke(cli.app(REGISTRY, fake.APP), list(args))
    assert out.exit_code == 0, out.output
    return out.output


def test_grammar_sample_prints_lines_and_outline(cfg, record):
    out = invoke("grammar", "sample", fake.SOURCE_ID, record.id, "--instructions")
    assert "Write the grammar" in out
    assert "== outline: none" in out
    assert "== page 0" in out
    assert "y80 x280 s11 B C g22 | Chapter 5" in out


def test_grammar_check_reports_without_writing(cfg, record):
    write_grammar(cfg)
    out = invoke("grammar", "check", fake.SOURCE_ID, record.id)
    assert "sections:  2" in out
    assert "sequence   5/5-3: gap: 5-1 -> 5-3" in out
    assert "derive:    held (not accepted; run grammar accept)" in out
    with Store(cfg).open(read_only=True) as conn:
        assert steps.stored(conn, TABULATE, NAME) == {}


def test_grammar_infer_dry_run_makes_no_request(cfg, record, monkeypatch):
    monkeypatch.setattr(infer, "backend", lambda *a, **k: pytest.fail("no request expected"))
    out = CliRunner().invoke(
        cli.app(REGISTRY, fake.APP), ["grammar", "infer", fake.SOURCE_ID, record.id, "--dry-run"]
    )
    assert out.exit_code == 0, out.output
    assert "1 pages, about" in out.output


def test_grammar_infer_needs_a_model(cfg, record, monkeypatch):
    monkeypatch.delenv("NOMOSCOPE_GRAMMAR_MODEL", raising=False)
    out = CliRunner().invoke(
        cli.app(REGISTRY, fake.APP), ["grammar", "infer", fake.SOURCE_ID, record.id]
    )
    assert out.exit_code == 1
    assert "set NOMOSCOPE_GRAMMAR_MODEL" in out.output


def test_grammar_infer_writes_a_draft(cfg, record, monkeypatch):
    seen: list = []
    reply = completion(None, ANSWER)
    real = infer.backend
    monkeypatch.setattr(
        infer, "backend", lambda n, k, effort: real(n, k, server(200, reply, seen), effort)
    )
    monkeypatch.setenv("NOMOSCOPE_GRAMMAR_MODEL", "m")
    monkeypatch.setenv(infer.KEY_ENV, "key")
    out = invoke("grammar", "infer", fake.SOURCE_ID, record.id)
    path = grammar_path(cruncher_grammars(), runner_row(cfg))
    assert path is not None
    assert gr.load(path).levels[0].name == "section"
    assert path.read_text().startswith("# Inferred by m")
    assert seen[0]["max_tokens"] == infer.MAX_TOKENS
    assert "tokens in" in out
    again = CliRunner().invoke(
        cli.app(REGISTRY, fake.APP), ["grammar", "infer", fake.SOURCE_ID, record.id]
    )
    assert again.exit_code == 1 and "exists" in again.output


def test_grammar_review_writes_a_page_beside_its_source(cfg, record):
    write_grammar(cfg)
    out = invoke("grammar", "review", fake.SOURCE_ID, record.id)
    page = Path(out.strip())
    assert page == cfg.data_root / "review-pages" / fake.SOURCE_ID / record.id / "index.html"
    text = page.read_text()
    assert 'href="code.json#page=1">section 0 p1</a>' in text
    assert "Permits are required." in text
    [linked] = [p for p in page.parent.iterdir() if p.is_symlink()]
    assert linked.read_bytes().startswith(b"[")


def test_attaching_a_record_reruns_the_parse(cfg, record):
    write_grammar(cfg)
    crunch(cfg)
    assert crunch(cfg) == []
    attach.write(cfg, SOURCE, record, "ocr", {"00000-00001.json": b"{}"})
    assert [(r.stage, r.unit) for r in crunch(cfg)] == [
        ("crunch", record.id),
        ("tabulate", record.id),
    ]


def test_a_changed_section_holds_the_accepted_record(cfg, record):
    write_grammar(cfg)
    accept(record)
    # Without the history rule, 5-1's history note joins its text.
    write_grammar(cfg, gr.Sections(drop=RULES.drop))
    crunch(cfg)
    details = tabulate_details(cfg, record)
    assert (details["admitted"], details["changed"]) == (False, 1)
    out = invoke("grammar", "check", fake.SOURCE_ID, record.id)
    assert "changed:   1 sections since review" in out
    assert "5-1          history, text" in out


def test_another_record_of_the_code_is_judged_on_its_findings(cfg, record):
    write_grammar(cfg)
    accept(record)
    amend(cfg, record="2020-01-01T000000Z-00000000")
    write_grammar(cfg, gr.Sections(drop=RULES.drop))
    crunch(cfg)
    details = tabulate_details(cfg, record)
    assert (details["admitted"], details["changed"]) == (True, 0)


def test_reviews_are_kept_and_the_latest_is_in_force(cfg, record):
    write_grammar(cfg)
    out = accept(record, "--note", "sequence: 5-2 is reserved")
    assert f"{record.id} accepted by t" in out
    assert "{'sequence': 1}; 2 sections" in out
    amend(cfg, note="again")
    catalog.build(cfg)
    with catalog.connect(cfg) as conn:
        rows = conn.execute("SELECT note FROM reviews ORDER BY reviewed_at").fetchall()
        [(sections,)] = conn.execute("SELECT count(*) FROM reviewed_sections").fetchall()
    assert rows == [("sequence: 5-2 is reserved",), ("again",)]
    assert sections == 4
    found = reviews.latest(cfg, fake.SOURCE_ID, JURISDICTION, "municipal")
    assert found is not None and found.note == "again"
