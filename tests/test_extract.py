import json
import os
from pathlib import Path

import polars as pl
import pytest

from nomoscope import chat, settings
from nomoscope.extract import (
    build,
    citations,
    definitions,
    enactments,
    locate,
    names,
    penalties,
    references,
    rows,
    run,
    scopes,
    store,
    tasks,
    units,
)
from nomoscope.extract import task as task_mod
from nomoscope.extract.citations import Cited
from nomoscope.graph.store import Store
from nomoscope.providers import ProviderError
from tests import fake
from tests.pipeline import P1, P2, P3, crunch, derive, publish

pytestmark = pytest.mark.usefixtures("conventions")
J = "us-il-windytown"
SETTINGS = task_mod.Settings(fake.CHAT)
# Two sections that print the same footer.
P5 = """\
publication 2025-03-01
edition Supp. No. 4
node 1 | Chapter 1 | General Provisions
section 1-1 | Title | Ord. 2019-12, passed 12-3-2019, eff. 1-1-2020
note running | Amended by Ord. 2025-2
This code is the Windytown Code.
section 1-3 | Penalty | Ord. 2025-2, eff. 3-1-2025
note running | Amended by Ord. 2025-2
(a) Any person who violates this chapter shall be fined not more than $250.
(b) Each day a violation continues is a separate offense, as provided in Section 1-1.
"""


@pytest.fixture
def windytown(cfg, tmp_path) -> rows.Rows:
    partial = {"completeness": "partial"}
    published = (
        (P1, "p1.txt", {}),
        (P2, "p2.txt", partial),
        (P3, "p3.txt", {}),
        (P5, "p5.txt", partial),
    )
    for text, name, params in published:
        publish(cfg, tmp_path, text, name, **params)
    crunch(cfg)
    derive(cfg)
    found = rows.load(cfg, J)
    assert found is not None
    return found


def configure(**tables: dict[str, str]) -> settings.Settings:
    path = Path(os.environ[settings.env_config(fake.APP)])
    lines = [
        f"[{name}]\n{''.join(f'{k} = {v!r}\n' for k, v in t.items())}" for name, t in tables.items()
    ]
    path.write_text(path.read_text() + "".join(lines).replace("'", '"'))
    return settings.load(fake.APP)


def reply(items: dict) -> chat.Completion:
    return chat.Completion(json.dumps(items), 100, 50)


def definitions_reply(messages: list[dict[str, str]]) -> chat.Completion:
    text = messages[-1]["content"]
    found = []
    if "\u201cCity\u201d" in text or '"City"' in text:
        city = "the City of Windytown" if "the City of" in text else "Windytown"
        found.append(
            {"term": "City", "scope": None, "text": f'"City" means {city}.', "confidence": 0.9}
        )
    return reply({"definitions": found, "governs": None})


def test_every_task_has_a_settings_table():
    assert set(tasks.BY_NAME) <= settings.KEYS


def test_quotes_are_found_with_straightened_marks_and_close_windows():
    body = (
        "(a) \u201cCity\u201d means  the City of Windytown.\n(b) \u201cCode\u201d means this code."
    )
    end = body.index("Windytown.") + len("Windytown.")
    assert locate.located(None, body, ['"City" means the City of Windytown.']) == [(4, end)]
    assert locate.locate(body, '"City" means the Cty of Windytown.') == (4, end)
    assert locate.locate(body, "nothing like it at all, really") is None
    twice = "this chapter and this chapter"
    assert locate.located(None, twice, ["this chapter"] * 3) == [(0, 12), (17, 29), None]
    assert locate.located("Chapter 1 > General", "text", ["Chapter 1 > General"]) == [None]


def test_name_tables_apply_to_the_jurisdictions_below(tmp_path):
    (tmp_path / "us-il.toml").write_text(
        '[codes]\n"Municipal Code" = "us-il/law/MUNI"\n"The Vehicle Code" = "us-il/law/VEH"\n'
        '[enactments]\n"Ord." = "ordinance"\n"P.A." = "us-il/public-act"\n'
    )
    (tmp_path / f"{J}.toml").write_text(
        '[codes]\n"municipal code" = "us-il-windytown/law/municipal"\n'
    )
    found = names.load(tmp_path)
    assert found.code(J, "Municipal  Code") == f"{J}/law/municipal"
    assert found.code("us-il-chicago", "municipal code") == "us-il/law/MUNI"
    assert found.code(J, "vehicle code") == "us-il/law/VEH"
    assert found.enactment(J, "ord.") == (J, "ordinance")
    assert found.enactment(J, "P.A.") == ("us-il", "public-act")
    assert names.lineage(J) == [J, "us-il", "us"]
    (tmp_path / "us.toml").write_text("[statutes]\n")
    with pytest.raises(Exception, match="unknown tables statutes"):
        names.load(tmp_path)


def test_units_hold_an_indicator_and_repeat_by_hash(windytown):
    found = units.units(definitions.TASK, windytown, SETTINGS.limit)
    assert set(found["source"]) == {"text"}
    assert all("means" in b for b in found["body"])
    notes = units.units(enactments.TASK, windytown, SETTINGS.limit)
    running = notes.filter(pl.col("source") == "running")
    # One footer printed with two sections: two units, one hash.
    assert running.height == 2 and running["content_sha256"].n_unique() == 1
    assert set(notes["source"]) >= {"history", "running", "publisher"}
    refs = units.units(references.TASK, windytown, SETTINGS.limit)
    penalty = refs.filter(pl.col("body").str.contains("Section 1-1"))
    assert penalty["context"][0].startswith("municipal 1-3 Penalty")


def test_a_run_estimates_then_stores_what_it_paid_for(cfg, windytown):
    estimate = run.run(cfg, definitions.TASK, SETTINGS, windytown, None)
    assert estimate.pending == 2 and estimate.dollars > 0 and estimate.sent == 0
    done = run.run(cfg, definitions.TASK, SETTINGS, windytown, definitions_reply, spend=1.0)
    assert (done.sent, done.items, done.failed, done.left) == (2, 2, 0, 0)
    assert done.spent == pytest.approx(2 * fake.CHAT.dollars(100, 50))
    again = run.run(cfg, definitions.TASK, SETTINGS, windytown, definitions_reply, spend=1.0)
    assert again.pending == 0
    held = store.held(cfg, definitions.NAME, fake.CHAT, J, definitions.PROMPT_VERSION)
    assert held["start_char"].to_list() == [4, 4]


def test_a_reply_that_never_fits_is_stored_and_a_busy_provider_left_pending(cfg, windytown):
    def bad(messages):
        return chat.Completion('{"definitions": "no"}', 10, 5)

    failed = run.run(cfg, definitions.TASK, SETTINGS, windytown, bad, spend=1.0)
    assert (failed.failed, failed.items) == (2, 0)
    held = store.held(cfg, definitions.NAME, fake.CHAT, J, definitions.PROMPT_VERSION)
    assert all(f.startswith("output did not validate") for f in held["failed"])

    def busy(messages):
        raise ProviderError("busy", 503)

    left = run.run(cfg, penalties.TASK, task_mod.Settings(fake.CHAT), windytown, busy, spend=1.0)
    assert left.left == left.sent > 0
    assert store.files(cfg, penalties.NAME, fake.CHAT, J) == []

    def refused(messages):
        raise ProviderError("bad request", 400)

    gone = run.run(cfg, penalties.TASK, task_mod.Settings(fake.CHAT), windytown, refused, spend=1.0)
    assert gone.failed == gone.sent > 0


def test_the_spend_bounds_a_run(cfg, windytown):
    one = run.cost(fake.CHAT, definitions.TASK, 40) * 1.5
    done = run.run(cfg, definitions.TASK, SETTINGS, windytown, definitions_reply, spend=one)
    assert done.sent == 1


def test_the_dataset_is_built_once_every_unit_is_extracted(cfg, windytown):
    cfg = configure(definitions={"model": fake.CHAT.slug})
    s = Store(cfg)
    [pending] = build.build(cfg, s, fake.REGISTRY.chat_models, None)
    assert (pending.rows, pending.pending) == (None, 2)
    run.run(cfg, definitions.TASK, SETTINGS, windytown, definitions_reply, spend=1.0)
    [built] = build.build(cfg, s, fake.REGISTRY.chat_models, None)
    assert built.rows == 2
    assert build.build(cfg, s, fake.REGISTRY.chat_models, None) == []
    from nomoscope import show

    [line] = [x for x in show.listing(cfg, fake.REGISTRY) if x.stage == "extract"]
    assert (line.scope, line.unit, line.state) == ("definitions", J, "fresh")
    frame = pl.read_parquet(cfg.derived / "definitions" / f"jurisdiction={J}" / "data.parquet")
    assert frame["term_key"].to_list() == ["city", "city"]
    assert set(frame["section_id"]) == {f"{J}/law/municipal/1-2"}
    assert frame["model"][0] == fake.CHAT.slug
    assert frame["id"][0].endswith("/def/0")
    assert build.views(cfg) == {"definitions": "derived/definitions/*/*.parquet"}
    from nomoscope.catalog import build as catalog

    catalog.build(cfg, build.views(cfg))
    conn = catalog.connect(cfg)
    try:
        found = conn.execute(
            "SELECT term_key, start_date, end_basis FROM definitions_in_force ORDER BY start_date"
        ).fetchall()
    finally:
        conn.close()
    assert [(t, str(d)) for t, d, _ in found] == [("city", "2020-01-01"), ("city", "2024-06-01")]


def test_a_changed_name_table_rebuilds(cfg, windytown, tmp_path):
    cfg = configure(definitions={"model": fake.CHAT.slug})
    run.run(cfg, definitions.TASK, SETTINGS, windytown, definitions_reply, spend=1.0)
    s = Store(cfg)
    tables = tmp_path / "citations"
    tables.mkdir()
    assert len(build.build(cfg, s, fake.REGISTRY.chat_models, tables)) == 1
    assert build.build(cfg, s, fake.REGISTRY.chat_models, tables) == []
    (tables / "us.toml").write_text('[codes]\n"u.s.c." = "us/law/USC"\n')
    assert len(build.build(cfg, s, fake.REGISTRY.chat_models, tables)) == 1


def cite(text: str, kind: str, **parts) -> Cited:
    given = {
        "code": None,
        "section": None,
        "path": (),
        "levels": (),
        "commencing_section": None,
        "enactment": None,
        "enactment_number": None,
        "enactment_year": None,
        "relative": False,
    }
    return Cited(text, kind, **{**given, **parts})


def test_citations_resolve_inside_and_outside_the_jurisdiction(windytown, tmp_path):
    (tmp_path / "us-il.toml").write_text(
        '[codes]\n"vehicle code" = "us-il/law/VEH"\n[enactments]\n"ord." = "ordinance"\n'
    )
    tables = names.load(tmp_path)
    idx = scopes.index(windytown)
    [penalty] = windytown.versions.filter(pl.col("section_num") == "1-3")["id"].to_list()

    def resolved(c: Cited, end: str | None = None):
        return citations.resolve(idx, tables, penalty, 10, c, end, ())

    own, _ = resolved(cite("Section 1-1", "section", section="1-1"))
    assert (own.kind, own.id, own.status) == ("section", f"{J}/law/municipal/1-1", "resolved")
    missing, _ = resolved(cite("Section 9-9", "section", section="9-9"))
    assert missing.status == "unresolved"
    outside, _ = resolved(
        cite(
            "Vehicle Code \u00a7 11-501(a)",
            "section",
            code="Vehicle Code",
            section="\u00a7 11-501",
            path=("a",),
        )
    )
    assert (outside.kind, outside.id, outside.status) == (
        "subdivision",
        "us-il/law/VEH/11-501/a",
        "unloaded",
    )
    unknown, _ = resolved(cite("Zoning Act \u00a7 5", "section", code="Zoning Act", section="5"))
    assert unknown == citations.NONE
    article, _ = resolved(
        cite("Vehicle Code, art. IX", "section", code="Vehicle Code", section="art. IX")
    )
    assert article == citations.NONE
    chapter, _ = resolved(cite("this chapter", "division", relative=True))
    assert (chapter.id, chapter.status) == (f"{J}/node/municipal/1", "resolved")
    sub, _ = resolved(cite("subsection (a)", "subdivision", path=("a",), relative=True))
    assert (sub.id, sub.status) == (f"{J}/law/municipal/1-3/a", "resolved")
    ordinance, _ = resolved(
        cite(
            "Ord. 2025-2",
            "enactment",
            enactment="Ord.",
            enactment_number="2025-2",
            enactment_year=2025,
        )
    )
    assert ordinance.id == f"{J}/enactment/ordinance/2025/2025-2"
    first, last = resolved(cite("Sections 1-1 to 1-3", "section", section="1-1"), "1-3")
    assert (first.id, last.id) == (f"{J}/law/municipal/1-1", f"{J}/law/municipal/1-3")


def test_scopes_resolve_to_the_rows_and_a_general_penalty_covers_its_code(windytown):
    idx = scopes.index(windytown)
    [penalty] = windytown.versions.filter(pl.col("section_num") == "1-3")["id"].to_list()
    chapter = scopes.resolve(idx, penalty, 10, "this chapter")
    assert (chapter.kind, chapter.id, chapter.basis) == ("node", f"{J}/node/municipal/1", "stated")
    section = scopes.resolve(idx, penalty, 10, "this section")
    assert section.id == f"{J}/law/municipal/1-3"
    assert scopes.resolve(idx, penalty, 10, "the whole world").kind == "other"
    general = penalties.Penalty(
        conduct=None,
        scope=None,
        general=True,
        classification="violation",
        character="unstated",
        fine_min=None,
        fine_max=250,
        fine_per="offense",
        imprisonment_max_days=None,
        offense="any",
        each_day_separate=True,
        other=[],
        text="x",
        confidence=1.0,
    )
    found = penalties.scope_of(idx, penalty, 10, general)
    assert (found.kind, found.id, found.basis) == ("code", f"{J}/law/municipal", "default")


def enactments_reply(messages: list[dict[str, str]]) -> chat.Completion:
    found = []
    if "Running header or footer" in messages[-1]["content"]:
        found.append(
            {
                "instrument": "Ord. 2025-2",
                "enactment": "Ord.",
                "number": "2025-2",
                "year": 2025,
                "action": "amended",
                "adopted_date": None,
                "effective_date": None,
                "filed_date": None,
                "subdivision": None,
                "text": "Amended by Ord. 2025-2",
                "confidence": 0.9,
            }
        )
    return reply({"enactments": found})


def test_a_footer_is_sent_once_and_its_items_join_every_section_it_is_printed_with(
    cfg, windytown, tmp_path
):
    tables = tmp_path / "citations"
    tables.mkdir()
    (tables / "us.toml").write_text('[enactments]\n"ord." = "ordinance"\n')
    cfg = configure(enactments={"model": fake.CHAT.slug})
    calls = []

    def counted(messages):
        calls.append(messages[-1]["content"])
        return enactments_reply(messages)

    run.run(cfg, enactments.TASK, SETTINGS, windytown, counted, spend=1.0)
    assert sum("Running header or footer" in c for c in calls) == 1
    [built] = build.build(cfg, Store(cfg), fake.REGISTRY.chat_models, tables)
    frame = pl.read_parquet(cfg.derived / "enactments" / f"jurisdiction={J}" / "data.parquet")
    footers = frame.filter(pl.col("source") == "running")
    assert sorted(footers["section_id"]) == [f"{J}/law/municipal/1-1", f"{J}/law/municipal/1-3"]
    assert set(footers["enactment_id"]) == {f"{J}/enactment/ordinance/2025/2025-2"}
    assert built.rows == frame.height


def test_the_extract_command_estimates_without_a_spend(windytown):
    from typer.testing import CliRunner

    from nomoscope import cli

    configure(definitions={"model": fake.CHAT.slug})
    app = cli.app(fake.REGISTRY, fake.APP)
    out = CliRunner().invoke(app, ["extract", "definitions", "-j", J])
    assert out.exit_code == 0, out.output
    assert "2 pending" in out.output and fake.CHAT.slug in out.output
    bad = CliRunner().invoke(app, ["extract", "defs"])
    assert bad.exit_code == 1 and "unknown task 'defs'" in bad.output


def test_the_extract_command_builds_only_its_own_task(windytown, monkeypatch):
    from typer.testing import CliRunner

    from nomoscope import cli

    configure(definitions={"model": fake.CHAT.slug}, penalties={"model": fake.CHAT.slug})
    monkeypatch.setenv("FIREWORKS_API_KEY", "test")
    monkeypatch.setattr(
        chat, "complete", lambda http, model, messages, schema: definitions_reply(messages)
    )
    app = cli.app(fake.REGISTRY, fake.APP)
    out = CliRunner().invoke(app, ["extract", "definitions", "-j", J, "--spend", "1"])
    assert out.exit_code == 0, out.output
    assert "definitions" in out.output and "2 rows" in out.output
    assert "penalties" not in out.output


def test_every_dataset_name_is_a_usable_view_name():
    import duckdb

    for t in tasks.TASKS:
        duckdb.sql(f"CREATE TEMP VIEW {t.dataset} AS SELECT 1 AS x")
