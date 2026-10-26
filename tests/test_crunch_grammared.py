import json
from datetime import date
from pathlib import Path

import polars as pl
import pytest
from typer.testing import CliRunner

from nomoscope import cli
from nomoscope.crunch import CrunchError, runner
from nomoscope.crunch.grammared import Document, GrammarCruncher, grammar_path
from nomoscope.crunch.layout import Layout
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.routes import load as load_routes
from nomoscope.derive import build
from nomoscope.graph import steps
from nomoscope.graph.steps import CARVE
from nomoscope.graph.store import Store
from nomoscope.partition import dataset_path
from nomoscope.settings import Settings
from nomoscope.snarf import archive, local
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


REGISTRY = Registry(
    sources={fake.SOURCE_ID: fake.SOURCE},
    crunchers={NAME: GrammarCruncher(read, "1")},
)


@pytest.fixture
def record(cfg, tmp_path) -> archive.Record:
    assert cfg.config_root is not None
    (cfg.config_root / "crunchers.csv").write_text(
        f"source,jurisdiction,law_code,cruncher,valid_from\nfake,*,*,{NAME},\n"
    )
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


def write_grammar(cfg: Settings, review: gr.Review | None = None) -> None:
    row = runner_row(cfg)
    path = grammar_path(cfg, row)
    assert path is not None
    path.parent.mkdir(parents=True, exist_ok=True)
    g = GRAMMAR.model_copy(update={"sections": RULES, "review": review or gr.Review()})
    path.write_text(gr.dumps(g))


def runner_row(cfg: Settings):
    store = Store(cfg)
    runner.register(cfg, store, REGISTRY)
    with store.open(read_only=True) as conn:
        [routed] = runner.plans(cfg, conn, REGISTRY, load_routes(cfg))
    [row] = routed.rows.values()
    return row


def crunch(cfg: Settings) -> list:
    return runner.crunch(cfg, Store(cfg), REGISTRY)


def carve_details(cfg: Settings, record: archive.Record):
    with Store(cfg).open(read_only=True) as conn:
        return steps.stored(conn, CARVE, NAME)[record.id].details


def derived(cfg: Settings) -> list[build.BuildResult]:
    return build.build(cfg, Store(cfg))


def test_a_record_without_a_grammar_names_the_path(cfg, record):
    with pytest.raises(CrunchError, match=f"grammars/fake/{JURISDICTION}/municipal.toml"):
        crunch(cfg)


def test_a_draft_grammar_carves_but_derive_holds(cfg, record):
    write_grammar(cfg)
    results = crunch(cfg)
    assert [(r.stage, r.unit) for r in results] == [("crunch", record.id), ("carve", record.id)]
    details = carve_details(cfg, record)
    assert details is not None
    assert details["admitted"] is False
    assert details["findings"] == {"sequence": 1}
    assert derived(cfg) == []
    with Store(cfg).open(read_only=True) as conn:
        counts = runner.pending(cfg, conn, REGISTRY, load_routes(cfg))
    assert counts[NAME]["held"] == 1


def test_review_admits_and_derive_builds(cfg, record):
    write_grammar(cfg)
    crunch(cfg)
    accepted = gr.Review(status="reviewed", by="t", on=date(2026, 10, 26), findings={"sequence": 1})
    write_grammar(cfg, accepted)
    assert [r.stage for r in crunch(cfg)] == ["crunch", "carve"]
    assert carve_details(cfg, record)["admitted"] is True
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
    write_grammar(cfg, gr.Review(status="reviewed", findings={}))
    crunch(cfg)
    details = carve_details(cfg, record)
    assert details["admitted"] is False
    assert details["worse"] == {"sequence": 1}


def test_sections_carry_text_history_and_drop_notes(cfg, record):
    write_grammar(cfg)
    crunch(cfg)
    path = Layout(NAME).carved_table(cfg, "law_sections", record.id)
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
    assert "derive:    held (review draft" in out
    with Store(cfg).open(read_only=True) as conn:
        assert steps.stored(conn, CARVE, NAME) == {}


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
    path = grammar_path(cfg, runner_row(cfg))
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
    assert page == cfg.data_root / "review" / fake.SOURCE_ID / record.id / "index.html"
    text = page.read_text()
    assert 'href="code.json#page=1">section 0 p1</a>' in text
    assert "Permits are required." in text
    [linked] = [p for p in page.parent.iterdir() if p.is_symlink()]
    assert linked.read_bytes().startswith(b"[")
