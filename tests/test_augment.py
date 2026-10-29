import json
from datetime import date

import pytest

from nomoscope import chat
from nomoscope.answer import ask, augment, group, prompt, store
from nomoscope.answer.models import AnswerError
from nomoscope.answer.prompt import DEFINITION, PENALTY, REFERENCE
from nomoscope.catalog import build as catalog
from nomoscope.extract import build, definitions, penalties, references, rows, run
from nomoscope.extract import task as task_mod
from nomoscope.graph.store import Store
from nomoscope.lookup.run import Found, Looked
from nomoscope.search import query
from nomoscope.search.query import Hit
from tests import fake
from tests.extracting import configure, reply
from tests.pipeline import crunch, derive, publish

pytestmark = pytest.mark.usefixtures("conventions")
J = "us-il-windytown"
ON = date(2025, 6, 1)
CODE = """\
publication 2025-02-01
edition Supp. No. 5
node 1 | Chapter 1 | General Provisions
section 1-1 | Title | Ord. 1
This code is the Windytown Code.
section 1-2 | Definitions | Ord. 1
(a) As used in this chapter, "Lot" means a parcel of land.
(b) "Code" means this code.
section 1-3 | Fences | Ord. 2
(a) No fence on a lot shall exceed six feet, except as provided in Section 1-4.
(b) A fence described in subsection (a) needs no permit.
section 1-4 | Corner lots | Ord. 2
A fence on a corner lot may not exceed three feet, notwithstanding this chapter.
section 1-5 | Penalties | Ord. 2
(a) Any person who violates this chapter shall be fined not more than $250 for each day.
(b) Absent another penalty, a violation of this code is punishable by a fine of up to $100.
node 2 | Chapter 2 | Animals
section 2-1 | Dogs | Ord. 3
No dog shall run at large on a lot, as Chapter 1 provides.
section 2-2 | Dog penalties | Ord. 3
A dog owner in violation is subject to a fine of $50.
section 2-3 | Kennels | Ord. 3
Kennels are licensed under Town Law \u00a7 130(a) and Town Law \u00a7 131.
"""
DEFINED = {
    '"Lot" means a parcel of land.': ("Lot", "this chapter"),
    '"Code" means this code.': ("Code", None),
}
CITED = {
    "Section 1-4": {"kind": "section", "section": "1-4", "path": [], "relative": False},
    "subsection (a)": {"kind": "subdivision", "section": None, "path": ["a"], "relative": True},
    "this chapter": {"kind": "division", "section": None, "path": [], "relative": True},
    "Town Law \u00a7 130(a)": {
        "kind": "subdivision",
        "code": "Town Law",
        "section": "130",
        "path": ["a"],
        "relative": False,
        "bluebook": "Ill. Town Law \u00a7 130(a)",
    },
    "Town Law \u00a7 131": {
        "kind": "section",
        "code": "Town Law",
        "section": "131",
        "path": [],
        "relative": False,
        "bluebook": "Ill. Town Law \u00a7 131",
    },
    "Chapter 1 provides": {
        "kind": "division",
        "section": None,
        "path": [],
        "relative": False,
        "levels": [{"level": "chapter", "number": "Chapter 1"}],
    },
}


PENALIZED = {
    "Any person who violates this chapter shall be fined not more than $250 for each day.": {
        "scope": "this chapter",
        "general": False,
        "fine_max": 250,
        "fine_per": "day",
    },
    "a violation of this code is punishable by a fine of up to $100.": {
        "scope": None,
        "general": True,
        "fine_max": 100,
        "fine_per": "offense",
    },
    "A dog owner in violation is subject to a fine of $50.": {
        "scope": None,
        "general": False,
        "fine_max": 50,
        "fine_per": "offense",
    },
}


def penalties_reply(messages):
    text = messages[-1]["content"]
    found = [
        {
            "conduct": None, "classification": "violation", "character": "unstated",
            "fine_min": None, "imprisonment_max_days": None, "offense": "any",
            "each_day_separate": False, "other": [], "text": quote, "confidence": 0.9, **parts,
        }
        for quote, parts in PENALIZED.items()
        if quote in text
    ]  # fmt: skip
    return reply({"penalties": found})


def definitions_reply(messages):
    text = messages[-1]["content"]
    found = [
        {"term": term, "scope": scope, "text": quote, "confidence": 0.9}
        for quote, (term, scope) in DEFINED.items()
        if quote in text
    ]
    return reply({"definitions": found, "governs": None})


def references_reply(messages):
    body = messages[-1]["content"].split("\n\n", 1)[-1]
    found = []
    for quote, parts in CITED.items():
        if quote in body:
            found.append(
                {
                    "text": quote, "code": None, "section_end": None, "path_end": [],
                    "levels": [], "commencing_section": None, "enactment": None,
                    "enactment_number": None, "enactment_year": None, "bluebook": None,
                    "confidence": 0.9, **parts,
                }
            )  # fmt: skip
    return reply({"references": found})


@pytest.fixture
def conn(cfg, tmp_path):
    publish(cfg, tmp_path, CODE, "code.txt")
    crunch(cfg)
    derive(cfg)
    model = {"model": fake.CHAT.slug}
    cfg = configure(definitions=model, references=model, penalties=model)
    found = rows.load(cfg, J)
    assert found is not None
    settings = task_mod.Settings(fake.CHAT)
    run.run(cfg, definitions.TASK, settings, found, definitions_reply, spend=1.0)
    run.run(cfg, references.TASK, settings, found, references_reply, spend=1.0)
    run.run(cfg, penalties.TASK, settings, found, penalties_reply, spend=1.0)
    tables = tmp_path / "citations"
    tables.mkdir()
    (tables / "us-il.toml").write_text('[codes]\n"town law" = "us-il/law/TWN"\n')
    assert len(build.build(cfg, Store(cfg), fake.REGISTRY.chat_models, tables)) == 3
    catalog.build(cfg, build.views(cfg))
    c = catalog.connect(cfg)
    yield c
    c.close()


def passages(conn, *sections: str) -> list[prompt.Passage]:
    found = query.candidates(conn, J, ON)
    by_section = {r["section_num"]: r for r in found.iter_rows(named=True)}
    hits = [
        Hit(
            rank=i, law_code=r["law_code"], section_num=r["section_num"], path=r["path"] or "",
            heading=r["heading"], snippet=r["body"], law_version_id=r["block_id"],
            segment_id=r["segment_id"], score=1.0, context=r["context"],
        )
        for i, r in enumerate((by_section[s] for s in sections), start=1)
    ]  # fmt: skip
    return prompt.passages(hits)


BOTH = augment.Settings(definitions=True, references=True)
ALL = augment.Settings(definitions=True, references=True, penalties=True)


def notes_of(p: prompt.Passage) -> list[tuple[str, str, str | None]]:
    return [(n.note.kind, n.note.name, n.given_under) for n in p.notes]


def test_a_chapter_definition_reaches_other_sections_of_its_chapter_only(conn):
    fences, dogs = augment.augment(conn, J, ON, BOTH, passages(conn, "1-3", "2-1"))[0]
    assert (DEFINITION, "Lot", None) in notes_of(fences)
    [lot] = [n.note for n in fences.notes if n.note.name == "Lot"]
    assert lot.text == '"Lot" means a parcel of land.'
    assert lot.about == "municipal 1-2, applies to Chapter 1 General Provisions"
    assert not [n for n in dogs.notes if n.note.kind == DEFINITION]


def test_an_unscoped_definition_stays_in_its_section(conn):
    [title] = augment.augment(conn, J, ON, BOTH, passages(conn, "1-1"))[0]
    assert "Code" not in [n.note.name for n in title.notes]


def test_a_definition_printed_in_the_passage_gets_no_note(conn):
    [defs] = augment.augment(conn, J, ON, BOTH, passages(conn, "1-2"))[0]
    assert not [n for n in defs.notes if n.note.kind == DEFINITION]


def test_a_cited_section_is_added_and_a_citation_of_the_passage_itself_is_not(conn):
    [fences] = augment.augment(conn, J, ON, BOTH, passages(conn, "1-3"))[0]
    [cited] = [n.note for n in fences.notes if n.note.kind == REFERENCE]
    assert cited.name == "municipal 1-4"
    assert cited.about == 'cited as "Section 1-4"'
    assert cited.text is not None and cited.text.startswith("A fence on a corner lot")


def test_a_citation_of_the_passages_own_heading_gets_no_note(conn):
    [corner] = augment.augment(conn, J, ON, BOTH, passages(conn, "1-4"))[0]
    assert not [n for n in corner.notes if n.note.kind == REFERENCE]


def test_a_cited_heading_is_named_without_text(conn):
    [dogs] = augment.augment(conn, J, ON, BOTH, passages(conn, "2-1"))[0]
    [cited] = [n.note for n in dogs.notes if n.note.kind == REFERENCE]
    assert (cited.name, cited.text) == ("Chapter 1 General Provisions", None)
    assert '- Chapter 1 General Provisions (cited as "Chapter 1 provides")' in prompt.render(dogs)


def test_a_note_given_once_is_a_pointer_after(conn):
    fences, corner = augment.augment(conn, J, ON, BOTH, passages(conn, "1-3", "1-4"))[0]
    assert (DEFINITION, "Lot", None) in notes_of(fences)
    assert (DEFINITION, "Lot", "P1") in notes_of(corner)
    assert (REFERENCE, "municipal 1-4", None) in notes_of(fences)
    text = prompt.render(corner)
    assert '- "Lot": defined under [P1]' in text
    assert "Defined terms used in this passage:" in text


def test_the_caps_bound_notes_and_their_length(conn):
    small = augment.Settings(definitions=True, references=True, note_chars=12, notes=1)
    [fences] = augment.augment(conn, J, ON, small, passages(conn, "1-3"))[0]
    [only] = fences.notes
    assert only.note.kind == DEFINITION and only.note.text == '"Lot" means ...'


def test_switched_off_passages_are_unchanged(conn):
    found = passages(conn, "1-3")
    assert augment.augment(conn, J, ON, augment.Settings(), found)[0] == found
    assert "Defined terms" not in prompt.render(found[0])


def test_a_missing_dataset_is_named_with_its_command(cfg, conn):
    augment.check(cfg, BOTH, [J])
    with pytest.raises(AnswerError, match="for us-x; build it with `extract definitions`"):
        augment.check(cfg, augment.Settings(definitions=True), [J, "us-x"])
    augment.check(cfg, augment.Settings(), ["us-x"])


def test_the_narrowest_definition_wins():
    place = augment.Place("j/law/c/5", "j/law/c", {"j/node/c/2": 1, "j/node/c": 2}, 0, 100)

    def d(scope_kind, scope_id, text):
        return augment.Definition(
            text, "Lot", "lot", text, "j/law/c/1", "c 1", scope_kind, scope_id, None
        )

    found = [
        d("code", "j/law/c", "wide"),
        d("node", "j/node/c", "chapter"),
        d("node", "j/node/c/2", "article"),
    ]
    hit = Hit(1, "c", "5", "", None, "A lot.", "v", "s", 1.0)
    [note] = augment.definition_notes(found, hit, place, 100)
    assert note.text == "article"


def test_stored_answers_list_their_notes(cfg, conn):
    found = passages(conn, "1-3", "1-4")
    body = '{"answer": "No.", "rationale": "[P1].", "citations": ["P1"], "confidence": 0.5}'
    g = group.single("Fences?")
    asked = ask.ask_group(
        g, J, ON, model=fake.CHAT, passages=2,
        retrieve=lambda q: [p.hit for p in found], complete=lambda m: chat.Completion(body, 10, 5),
        spend=1.0, augment=lambda ps: augment.augment(conn, J, ON, BOTH, ps),
    )  # fmt: skip
    store.write(cfg, g, asked, "test", config={"answers": {"definitions": True}})
    # DuckDB reuses an open database for the same path, so the old catalog
    # must be closed before the rebuilt one is opened.
    conn.close()
    catalog.build(cfg, {**build.views(cfg), **store.views(cfg)})
    c = catalog.connect(cfg)
    try:
        notes = c.execute(
            "SELECT n.label, n.kind, n.given_under FROM answers, unnest(notes) AS t(n)"
        ).fetchall()
        messages, config = c.execute("SELECT messages, config FROM answers").fetchone()
    finally:
        c.close()
    assert ("P1", "definition", None) in notes and ("P2", "definition", "P1") in notes
    assert ("P1", "reference", None) in notes
    system, user = json.loads(messages)
    assert system["role"] == "system" and '- "Lot": defined under [P1]' in user["content"]
    assert json.loads(config) == {"answers": {"definitions": True}}


def test_answers_written_before_notes_still_read(cfg):
    import polars as pl

    from nomoscope.parquet import write_parquet

    old = pl.DataFrame(
        {"question_id": ["Q1"], "answer": ["Yes."]},
        schema={"question_id": pl.String, "answer": pl.String},
    )
    write_parquet(old, store.folder(cfg, "g", J) / "old.parquet")
    new = old.with_columns(pl.lit([], dtype=pl.List(store.NOTE)).alias("notes"))
    write_parquet(new, store.folder(cfg, "g", J) / "new.parquet")
    catalog.build(cfg, store.views(cfg))
    c = catalog.connect(cfg)
    try:
        assert c.execute("SELECT count(*), count(notes) FROM answers").fetchone() == (2, 1)
    finally:
        c.close()


def test_notes_raise_the_estimate():
    plain = ask.estimate(fake.CHAT, 12, 1)
    assert ask.estimate(fake.CHAT, 12, 1, notes=True) > plain
    assert ask.options({"definitions": True, "notes": 4}).augment == augment.Settings(
        definitions=True, notes=4
    )


def test_penalties_that_cover_a_passage_are_noted_narrowest_first(conn):
    fences, dogs = augment.augment(conn, J, ON, ALL, passages(conn, "1-3", "2-1"))[0]
    names = [(n.note.name, n.note.about) for n in fences.notes if n.note.kind == PENALTY]
    assert names == [
        ("Violation; fine up to $250 per day",
         "municipal 1-5, applies to Chapter 1 General Provisions"),
        ("Violation; fine up to $100", "municipal 1-5, the general penalty for the code"),
    ]  # fmt: skip
    dog_fines = [n.note.name for n in dogs.notes if n.note.kind == PENALTY]
    # 2-2's penalty states no scope, so it stays in 2-2; the general one reaches 2-1.
    assert dog_fines == ["Violation; fine up to $100"]
    assert "Penalties that apply to this passage:" in prompt.render(fences)
    later = augment.augment(conn, J, ON, ALL, passages(conn, "1-3", "1-4"))[0][1]
    text = prompt.render(later)
    assert text.count("- The penalties given under [P1] also apply") == 1


def test_a_penalty_the_passage_prints_is_not_noted_again(conn):
    [own] = augment.augment(conn, J, ON, ALL, passages(conn, "2-2"))[0]
    assert [n.note.name for n in own.notes if n.note.kind == PENALTY] == [
        "Violation; fine up to $100"
    ]


def looking_up(found: dict[str, str]):
    """A stub lookup: text for the target ids in `found`, a search counted per call."""
    calls = []

    def lookup(request, may_search):
        calls.append((request.target_id, may_search))
        if not may_search:
            return Looked(None)
        text = found.get(request.target_id)
        hit = (
            Found(text, "https://law.justia.com/il/town/130", "T", "subdivision", ON)
            if text
            else None
        )
        return Looked(hit, 0.02, searched=True)

    return lookup, calls


LOOKUPS = augment.Settings(lookups=True, lookup_limit=4)


def test_an_unloaded_citation_gets_text_from_a_lookup(conn):
    lookup, calls = looking_up({"us-il/law/TWN/130/a": "(a) Kennels need a license."})
    [kennels], spent = augment.augment(conn, J, ON, LOOKUPS, passages(conn, "2-3"), lookup=lookup)
    [note] = [n.note for n in kennels.notes]
    assert note.name == "Ill. Town Law \u00a7 130(a)" and note.text == "(a) Kennels need a license."
    assert "current text from law.justia.com, retrieved 2025-06-01" in note.about
    assert sorted(t for t, _ in calls) == ["us-il/law/TWN/130/a", "us-il/law/TWN/131"]
    assert spent == pytest.approx(0.04)


def test_the_lookup_limit_leaves_the_rest_to_the_cache(conn):
    lookup, calls = looking_up({})
    one = augment.Settings(lookups=True, lookup_limit=1)
    augment.augment(conn, J, ON, one, passages(conn, "2-3"), lookup=lookup)
    assert [may for _, may in calls] == [True, False]


def test_lookup_costs_are_part_of_an_answers_dollars():
    from tests.test_answer import GLM, HITS, answering, reply_json

    complete, _ = answering(reply_json("Yes."))
    out = ask.ask(
        group.single("q").questions[0], [], "us-x", ON, model=GLM,
        retrieve=lambda q: HITS, complete=complete, augment=lambda ps: (ps, 0.25),
    )  # fmt: skip
    assert out.dollars == pytest.approx(GLM.dollars(1000, 100) + 0.25)
    assert ask.estimate(GLM, 12, 1, lookups=0.1) == pytest.approx(ask.estimate(GLM, 12, 1) + 0.1)
