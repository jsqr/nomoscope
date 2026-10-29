"""A fake source and cruncher for the tests.

A publication is one imported text file. The import's params give the
jurisdiction and code. The file's lines:

    publication 2024-01-15          the stated date (optional)
    edition Supp. No. 1             the edition (optional)
    node 1 | Chapter 1 | General    a heading: treepath | number | heading
    section 1-1 | Title | Ord. 1, eff. 1-1-2020
    (a) text lines of the section, up to the next node or section
    note running | Supp. No. 3     a note of the section or heading above it

A section belongs to the node above it. A partial publication
(completeness=partial) carries only changed sections.
"""

from collections.abc import Iterator, Mapping
from datetime import date

import polars as pl

from nomoscope.chat import ChatModel
from nomoscope.crunch import schemas
from nomoscope.crunch.registry import Registry
from nomoscope.crunch.routes import ANY, Route
from nomoscope.crunch.runner import Tabulator
from nomoscope.graph.records import RecordRow
from nomoscope.providers import FIREWORKS
from nomoscope.settings import Settings
from nomoscope.snarf import archive
from nomoscope.snarf.feeds import Feed, Source
from nomoscope.snarf.run import blob_store

APP = "nomoscope"
SOURCE_ID = "fake"
DRAFTING = "fake"
CODE = Feed("code", ("",), "none", days=(), completeness="full")
SOURCE = Source(SOURCE_ID, (CODE,), (), lambda d: {})


def tabulate(cfg: Settings, row: RecordRow, record: archive.Record) -> Mapping[str, pl.DataFrame]:
    [item] = record.items()
    text = blob_store(cfg).read(item.sha256).decode("utf-8")
    stated, nodes, sections, notes = parse(text)
    j, code = row.jurisdiction, row.code or "municipal"
    return {
        schemas.LAW_SECTIONS: pl.DataFrame(
            [
                {
                    "jurisdiction": j,
                    "law_code": code,
                    "section_num": s["num"],
                    "node_treepath": s["treepath"],
                    "heading": s["heading"],
                    "history": s["history"],
                    "text": s["text"],
                    "blob_sha256": item.sha256,
                    "drafting": DRAFTING,
                }
                for s in sections
            ],
            schema={
                "jurisdiction": pl.String,
                "law_code": pl.String,
                "section_num": pl.String,
                "node_treepath": pl.String,
                "heading": pl.String,
                "history": pl.String,
                "text": pl.String,
                "blob_sha256": pl.String,
                "drafting": pl.String,
            },
        ),
        schemas.LAW_NODES: pl.DataFrame(
            [
                {
                    "jurisdiction": j,
                    "law_code": code,
                    "node_treepath": n["treepath"],
                    "parent_treepath": n["treepath"].rpartition(".")[0] or None,
                    "level": n["number"].split()[0].lower(),
                    "number": n["number"],
                    "heading": n["heading"],
                    "depth": n["treepath"].count(".") + 1,
                    "position": i,
                    "sequence": i,
                    "contains_sections": True,
                }
                for i, n in enumerate(nodes)
            ],
            schema={
                "jurisdiction": pl.String,
                "law_code": pl.String,
                "node_treepath": pl.String,
                "parent_treepath": pl.String,
                "level": pl.String,
                "number": pl.String,
                "heading": pl.String,
                "depth": pl.Int64,
                "position": pl.Int64,
                "sequence": pl.Int64,
                "contains_sections": pl.Boolean,
            },
        ),
        schemas.LAW_PUBLICATIONS: schemas.publication(row, [code], stated),
        **(
            {
                schemas.LAW_NOTES: pl.DataFrame(
                    [{"jurisdiction": j, "law_code": code, **n} for n in notes],
                    schema=schemas.tabulated("UTC")[schemas.LAW_NOTES],
                )
            }
            if notes
            else {}
        ),
    }


CRUNCHER = Tabulator(tabulate, "1")
CHAT = ChatModel("test-chat", FIREWORKS, "test/chat", 1e-6, 4e-6)
REGISTRY = Registry(
    sources={SOURCE_ID: SOURCE},
    crunchers={SOURCE_ID: CRUNCHER},
    routes=(Route(SOURCE_ID, ANY, ANY, SOURCE_ID),),
    chat_models={CHAT.slug: CHAT},
)


def parse(text: str) -> tuple[schemas.Stated, list[dict], list[dict], list[dict]]:
    published = edition = None
    nodes: list[dict] = []
    sections: list[dict] = []
    notes: list[dict] = []
    above: dict[str, str | None] = {}
    for kind, rest in lines(text):
        if kind == "publication":
            published = date.fromisoformat(rest)
        elif kind == "edition":
            edition = rest
        elif kind == "node":
            treepath, number, heading = (p.strip() for p in rest.split("|"))
            nodes.append({"treepath": treepath, "number": number, "heading": heading})
            above = {"section_num": None, "node_treepath": treepath}
        elif kind == "section":
            num, heading, history = (p.strip() for p in rest.split("|"))
            sections.append(
                {
                    "num": num,
                    "heading": heading,
                    "history": history or None,
                    "treepath": nodes[-1]["treepath"] if nodes else None,
                    "body": [],
                }
            )
            above = {"section_num": num, "node_treepath": None}
        elif kind == "note":
            note_kind, _, note = rest.partition("|")
            notes.append({**above, "kind": note_kind.strip(), "text": note.strip()})
        else:
            sections[-1]["body"].append(rest)
    for s in sections:
        s["text"] = "\n".join(s.pop("body"))
    stated = schemas.Stated(published_date=published, edition=edition)
    return stated, nodes, sections, notes


def lines(text: str) -> Iterator[tuple[str, str]]:
    for line in text.splitlines():
        if not line.strip():
            continue
        head, _, rest = line.partition(" ")
        if head in ("publication", "edition", "node", "section", "note"):
            yield head, rest.strip()
        else:
            yield "text", line
