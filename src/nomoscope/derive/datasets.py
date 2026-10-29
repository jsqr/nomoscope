"""Build one jurisdiction's derived datasets from its tabulated tables.

Each publication's rows are recorded in five ledgers (see ledger):

- sections, keyed by text and node, which become law_observations;
- version payload, such as text and history;
- history notes, keyed by version and note, which become law_history;
- nodes, keyed by heading;
- ancestor edges of sections and nodes.

The datasets are built from the folded ledgers. Drafting conventions are
looked up by each section's drafting column.
"""

import hashlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass

import polars as pl

from nomoscope.crunch import schemas as tabulated_schemas
from nomoscope.crunch.schemas import SUBDIVISIONS
from nomoscope.derive import history, ids, ledger, registry, segment, subdivide
from nomoscope.derive.conventions import NONE, Conventions
from nomoscope.derive.ledger import SEEN

SECTION_KEY = ("law_code", "section_num", "version_id", "text_sha256", "node_treepath")
VERSION_KEY = ("law_code", "section_num", "version_id", "text_sha256")
VERSION_PAYLOAD = (
    "heading",
    "drafting",
    "part_code",
    "history",
    "division_history",
    "text",
    "markup",
    "line_indents",
    "subdivisions",
    "blob_sha256",
    "source_id",
    "effective_date",
    "statutory_default_date",
    "statute_year",
    "statute_session",
    "statute_chapter",
    "statute_section",
)
HISTORY_KEY = (*VERSION_KEY, "history")
HISTORY_PAYLOAD = ("drafting",)
NODE_KEY = ("law_code", "node_treepath", "level", "number", "heading")
NODE_PAYLOAD = (
    "parent_treepath",
    "depth",
    "position",
    "sequence",
    "contains_sections",
    "part_code",
)
ANCESTOR_KEY = ("law_code", "descendant_id", "ancestor_id", "depth")
NOTE_KEY = ("law_code", "section_num", "node_treepath", "kind", "text")
LINE_INDENTS = pl.List(pl.Int64)

PUBLICATION_SCHEMA = pl.Schema(
    {
        "publication": pl.String,
        "source": pl.String,
        "jurisdiction": pl.String,
        "law_codes": pl.List(pl.String),
        "published_date": pl.Date,
        "published_basis": pl.String,
        "completeness": pl.String,
        "edition": pl.String,
        "currency_text": pl.String,
        "currency_date": pl.Date,
        "session": pl.String,
        "sections": pl.Int64,
    }
)
SESSION_SCHEMA = pl.Schema(
    {
        "jurisdiction": pl.String,
        "session": pl.String,
        "kind": pl.String,
        "start_date": pl.Date,
        "end_date": pl.Date,
    }
)
ABSENT = {"absent_from": pl.Date}
OBSERVATION_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "section_id": pl.String,
        "node_id": pl.String,
        "jurisdiction": pl.String,
        "law_code": pl.String,
        "section_num": pl.String,
        "version_id": pl.String,
        "text_sha256": pl.String,
        "node_treepath": pl.String,
        **SEEN,
        **ABSENT,
    }
)
VERSION_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "section_id": pl.String,
        "jurisdiction": pl.String,
        "law_code": pl.String,
        "section_num": pl.String,
        "version_id": pl.String,
        "text_sha256": pl.String,
        "node_treepath": pl.String,
        "node_id": pl.String,
        "heading": pl.String,
        "drafting": pl.String,
        "history": pl.String,
        "division_history": pl.String,
        "text": pl.String,
        "markup": pl.String,
        "line_indents": LINE_INDENTS,
        "subdivisions": SUBDIVISIONS,
        "blob_sha256": pl.String,
        "source_id": pl.String,
        "statute_year": pl.Int64,
        "statute_session": pl.Int64,
        "statute_chapter": pl.Int64,
        "statute_section": pl.String,
        "part_code": pl.String,
        "effective_column": pl.Date,
        "enacted_date": pl.Date,
        "enacted_year": pl.Int64,
        "effective_date": pl.Date,
        "operative_date": pl.Date,
        "inoperative_date": pl.Date,
        "repeal_date": pl.Date,
        "statutory_default_date": pl.Date,
        **SEEN,
        **ABSENT,
        "successor_id": pl.String,
        "successor_effective_date": pl.Date,
    }
)
HISTORY_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "law_version_id": pl.String,
        "section_id": pl.String,
        "jurisdiction": pl.String,
        "law_code": pl.String,
        "section_num": pl.String,
        "version_id": pl.String,
        "text_sha256": pl.String,
        "history": pl.String,
        "drafting": pl.String,
        "enacted_date": pl.Date,
        "enacted_year": pl.Int64,
        "effective_date": pl.Date,
        "operative_date": pl.Date,
        "inoperative_date": pl.Date,
        "repeal_date": pl.Date,
        **SEEN,
    }
)
NODE_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "parent_id": pl.String,
        "jurisdiction": pl.String,
        "law_code": pl.String,
        "node_treepath": pl.String,
        "level": pl.String,
        "number": pl.String,
        "heading": pl.String,
        "parent_treepath": pl.String,
        "depth": pl.Int64,
        "position": pl.Int64,
        "sequence": pl.Int64,
        "contains_sections": pl.Boolean,
        "part_code": pl.String,
        **SEEN,
        **ABSENT,
    }
)
ANCESTOR_SCHEMA = pl.Schema(
    {
        "jurisdiction": pl.String,
        "law_code": pl.String,
        "descendant_id": pl.String,
        "ancestor_id": pl.String,
        "depth": pl.Int64,
        **SEEN,
        **ABSENT,
    }
)
NOTE_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "target_id": pl.String,
        "jurisdiction": pl.String,
        "law_code": pl.String,
        "section_num": pl.String,
        "node_treepath": pl.String,
        "kind": pl.String,
        "text": pl.String,
        **SEEN,
    }
)
SUBDIVISION_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "subdivision_id": pl.String,
        "parent_id": pl.String,
        "law_version_id": pl.String,
        "section_id": pl.String,
        "jurisdiction": pl.String,
        "path": pl.String,
        "level": pl.String,
        "style": pl.String,
        "number": pl.String,
        "depth": pl.Int64,
        "position": pl.Int64,
        "sequence": pl.Int64,
        "start_char": pl.Int64,
        "end_char": pl.Int64,
    }
)
SEGMENT_SCHEMA = pl.Schema(
    {
        "id": pl.String,
        "block_id": pl.String,
        "block_kind": pl.String,
        "jurisdiction": pl.String,
        "ordinal": pl.Int64,
        "start_char": pl.Int64,
        "end_char": pl.Int64,
        "chars": pl.Int64,
        "path": pl.String,
        "position": pl.Int64,
        "siblings": pl.Int64,
        "kind": pl.String,
        "piece": pl.Int64,
        "context": pl.String,
        "content_sha256": pl.String,
        "block_sha256": pl.String,
    }
)
CODES_SCHEMA = pl.Schema(
    {"publication": pl.String, **tabulated_schemas.tabulated("UTC")[tabulated_schemas.LAW_CODES]}
)
ATTACHMENTS_SCHEMA = pl.Schema(
    {
        "publication": pl.String,
        **tabulated_schemas.tabulated("UTC")[tabulated_schemas.LAW_ATTACHMENTS],
    }
)
SCHEMAS = {
    registry.PUBLICATIONS: PUBLICATION_SCHEMA,
    registry.SESSIONS: SESSION_SCHEMA,
    registry.LAW_CODES: CODES_SCHEMA,
    registry.LAW_ATTACHMENTS: ATTACHMENTS_SCHEMA,
    registry.LAW_OBSERVATIONS: OBSERVATION_SCHEMA,
    registry.LAW_VERSIONS: VERSION_SCHEMA,
    registry.LAW_HISTORY: HISTORY_SCHEMA,
    registry.LAW_NODES: NODE_SCHEMA,
    registry.LAW_ANCESTORS: ANCESTOR_SCHEMA,
    registry.LAW_SUBDIVISIONS: SUBDIVISION_SCHEMA,
    registry.SEGMENTS: SEGMENT_SCHEMA,
    registry.LAW_NOTES: NOTE_SCHEMA,
}
CONTEXT_SEP = " > "

# A heading can end with its range of sections, as in
# "ARTICLE 1. General Provisions [65920. - 65923.8.]". The range is dropped
# from segment context.
HEADING_RANGE = r"\s*\[[^\]]*\]\s*$"


@dataclass(frozen=True)
class Tabulated:
    """One publication's tabulated tables."""

    source: str
    publication: pl.DataFrame
    sections: pl.DataFrame | None = None
    nodes: pl.DataFrame | None = None
    sessions: pl.DataFrame | None = None
    codes: pl.DataFrame | None = None
    attachments: pl.DataFrame | None = None
    notes: pl.DataFrame | None = None


@dataclass(frozen=True)
class Ledgers:
    sections: pl.DataFrame | None
    payload: pl.DataFrame | None
    histories: pl.DataFrame | None
    nodes: pl.DataFrame | None
    ancestors: pl.DataFrame | None
    publications: pl.DataFrame
    sessions: pl.DataFrame | None
    # Not versioned: each publication's rows, the latest publication's kept.
    codes: pl.DataFrame | None = None
    attachments: pl.DataFrame | None = None
    notes: pl.DataFrame | None = None


def empty(schema: pl.Schema) -> pl.DataFrame:
    return pl.DataFrame(schema=schema)


def sighted(tabulated: Tabulated, jurisdiction: str) -> Ledgers:
    pub = tabulated.publication.row(0, named=True)
    stamp = {
        "publication": pub["publication"],
        "on": pub["published_date"],
        "completeness": pub["completeness"],
    }
    sections, nodes = tabulated.sections, tabulated.nodes
    return Ledgers(
        sections=ledger.sightings(sections, SECTION_KEY, **stamp) if sections is not None else None,
        payload=(
            ledger.sightings(sections, VERSION_KEY, VERSION_PAYLOAD, **stamp)
            if sections is not None
            else None
        ),
        histories=(
            ledger.sightings(
                sections.filter(pl.col("history").is_not_null()),
                HISTORY_KEY,
                HISTORY_PAYLOAD,
                **stamp,
            )
            if sections is not None
            else None
        ),
        nodes=ledger.sightings(nodes, NODE_KEY, NODE_PAYLOAD, **stamp)
        if nodes is not None
        else None,
        ancestors=ledger.sightings(
            ancestor_edges(sections, nodes, jurisdiction), ANCESTOR_KEY, **stamp
        ),
        publications=tabulated.publication.with_columns(
            pl.lit(tabulated.source).alias("source"),
            pl.lit(sections.height if sections is not None else 0, dtype=pl.Int64).alias(
                "sections"
            ),
        ).select(PUBLICATION_SCHEMA.names()),
        sessions=tabulated.sessions,
        codes=_stamped(tabulated.codes, pub["publication"], CODES_SCHEMA),
        attachments=_stamped(tabulated.attachments, pub["publication"], ATTACHMENTS_SCHEMA),
        notes=(
            ledger.sightings(tabulated.notes, NOTE_KEY, **stamp)
            if tabulated.notes is not None
            else None
        ),
    )


def _stamped(
    frame: pl.DataFrame | None, publication: str, schema: pl.Schema
) -> pl.DataFrame | None:
    if frame is None:
        return None
    return frame.with_columns(pl.lit(publication).alias("publication")).select(schema.names())


def _by_publication(a: pl.DataFrame | None, b: pl.DataFrame | None) -> pl.DataFrame | None:
    """Rows of both, a publication's rows from b replacing those from a."""
    if a is None or b is None:
        return b if a is None else a
    return pl.concat([a.filter(~pl.col("publication").is_in(b["publication"].implode())), b])


def merged(a: Ledgers, b: Ledgers) -> Ledgers:

    def fold(x, y, key, payload=()):
        return ledger.fold([f for f in (x, y) if f is not None], key, payload)

    publications = pl.concat([a.publications, b.publications], how="vertical_relaxed").unique(
        "publication", keep="last", maintain_order=True
    )
    sessions = [s for s in (a.sessions, b.sessions) if s is not None]
    return Ledgers(
        sections=fold(a.sections, b.sections, SECTION_KEY),
        payload=fold(a.payload, b.payload, VERSION_KEY, VERSION_PAYLOAD),
        histories=fold(a.histories, b.histories, HISTORY_KEY, HISTORY_PAYLOAD),
        nodes=fold(a.nodes, b.nodes, NODE_KEY, NODE_PAYLOAD),
        ancestors=fold(a.ancestors, b.ancestors, ANCESTOR_KEY),
        publications=publications,
        sessions=pl.concat(sessions, how="vertical_relaxed") if sessions else None,
        codes=_by_publication(a.codes, b.codes),
        attachments=_by_publication(a.attachments, b.attachments),
        notes=fold(a.notes, b.notes, NOTE_KEY),
    )


def folded(
    tabulated: Sequence[Tabulated], jurisdiction: str, onto: Ledgers | None = None
) -> Ledgers:
    """Fold each publication's ledgers onto onto."""
    found = onto
    for c in tabulated:
        one = sighted(c, jurisdiction)
        found = one if found is None else merged(found, one)
    if found is None:
        raise ValueError("no publications to fold")
    return found


def stored_ledgers(stored: Mapping[str, pl.DataFrame]) -> Ledgers:
    """The ledgers held in the stored datasets."""

    def part(dataset: str, columns: Sequence[str | pl.Expr]) -> pl.DataFrame | None:
        frame = stored.get(dataset)
        return frame.select(*columns, *SEEN.names()) if frame is not None and frame.height else None

    # The stored effective_date is derived. The payload is the tabulated value,
    # kept in effective_column.
    payload = [
        pl.col("effective_column").alias(c) if c == "effective_date" else pl.col(c)
        for c in (*VERSION_KEY, *VERSION_PAYLOAD)
    ]
    return Ledgers(
        sections=part(registry.LAW_OBSERVATIONS, SECTION_KEY),
        payload=part(registry.LAW_VERSIONS, payload),
        histories=part(registry.LAW_HISTORY, (*HISTORY_KEY, *HISTORY_PAYLOAD)),
        nodes=part(registry.LAW_NODES, (*NODE_KEY, *NODE_PAYLOAD)),
        ancestors=part(registry.LAW_ANCESTORS, ANCESTOR_KEY),
        publications=stored[registry.PUBLICATIONS].select(PUBLICATION_SCHEMA.names()),
        sessions=stored.get(registry.SESSIONS),
        codes=stored.get(registry.LAW_CODES),
        attachments=stored.get(registry.LAW_ATTACHMENTS),
        notes=part(registry.LAW_NOTES, NOTE_KEY),
    )


def ancestor_edges(
    sections: pl.DataFrame | None, nodes: pl.DataFrame | None, jurisdiction: str
) -> pl.DataFrame:
    """Every (descendant, ancestor) pair in one publication. Depth 1 is the parent.

    A section's ancestors start with its node.
    """
    j = jurisdiction
    code = pl.col("law_code")
    parent: dict[str, str | None] = {}
    codes: dict[str, str] = {}
    if nodes is not None:
        for row_id, parent_id, law_code in nodes.select(
            ids.law_node(j, code, pl.col("node_treepath")).alias("id"),
            ids.law_node(j, code, pl.col("parent_treepath")).alias("parent_id"),
            code,
        ).iter_rows():
            parent[row_id] = parent_id
            codes[row_id] = law_code
    if sections is not None:
        for row_id, node_id, law_code in sections.select(
            ids.law_section(j, code, pl.col("section_num")).alias("id"),
            ids.law_node(j, code, pl.col("node_treepath")).alias("node_id"),
            code,
        ).iter_rows():
            parent[row_id] = node_id
            codes[row_id] = law_code

    def chain(from_id: str | None) -> Iterator[str]:
        seen: set[str] = set()
        while from_id is not None and from_id not in seen:
            seen.add(from_id)
            yield from_id
            from_id = parent.get(from_id)

    rows = [(codes[d], d, a, k) for d in parent for k, a in enumerate(chain(parent[d]), 1)]
    schema = pl.Schema(
        {
            "law_code": pl.String,
            "descendant_id": pl.String,
            "ancestor_id": pl.String,
            "depth": pl.Int64,
        }
    )
    return pl.DataFrame(rows, schema=schema, orient="row")


def _kept(
    found: pl.DataFrame | None, pubs: pl.DataFrame, schema: pl.Schema, key: str
) -> pl.DataFrame:
    """The rows of publications still listed, sorted by publication date."""
    if found is None or not found.height:
        return empty(schema)
    order = pubs.select("publication", "published_date")
    return (
        found.join(order, on="publication", how="inner")
        .sort("published_date", "publication", key)
        .select(schema.names())
        .cast(schema)
    )


def publications(found: Ledgers) -> pl.DataFrame:
    return found.publications.cast(PUBLICATION_SCHEMA).sort("published_date", "publication")


def sessions(found: Ledgers, jurisdiction: str) -> pl.DataFrame:
    """The sessions the tabulations state, the latest statement of each, plus any
    session a publication names.
    """
    stated = (
        found.sessions.cast(SESSION_SCHEMA).unique("session", keep="last", maintain_order=True)
        if found.sessions is not None
        else empty(SESSION_SCHEMA)
    )
    named = (
        found.publications.select("session")
        .drop_nulls()
        .unique()
        .filter(~pl.col("session").is_in(stated["session"].implode()))
        .with_columns(pl.lit(jurisdiction).alias("jurisdiction"))
    )
    return (
        pl.concat([stated, named], how="diagonal_relaxed")
        .with_columns(pl.lit(jurisdiction).alias("jurisdiction"))
        .select(SESSION_SCHEMA.names())
        .cast(SESSION_SCHEMA)
        .sort("session")
    )


def law_observations(found: Ledgers, jurisdiction: str, census: pl.DataFrame) -> pl.DataFrame:
    if found.sections is None:
        return empty(OBSERVATION_SCHEMA)
    j, code, num = jurisdiction, pl.col("law_code"), pl.col("section_num")
    with_ids = found.sections.with_columns(
        ids.law_version(j, code, num, pl.col("version_id"), pl.col("text_sha256")).alias("id"),
        ids.law_section(j, code, num).alias("section_id"),
        ids.law_node(j, code, pl.col("node_treepath")).alias("node_id"),
        pl.lit(j).alias("jurisdiction"),
    )
    return ledger.with_absent_from(with_ids, census).select(OBSERVATION_SCHEMA.names())


def stated_dates(frame: pl.DataFrame, conventions: Conventions) -> list[pl.Series]:
    """The dates each row's history states, else its division's, and the enacted year."""
    inherited = (
        frame["division_history"]
        if "division_history" in frame.columns
        else pl.Series([None] * frame.height, dtype=pl.String)
    )
    stated = []
    for h, d, up in zip(frame["history"], frame["drafting"], inherited, strict=True):
        phrases = conventions.phrases.get(d) if d else None
        found = history.parse(h, phrases)
        stated.append(found if found != history.Dates() else history.parse(up, phrases))
    return [
        *(
            pl.Series(f"{aspect}_date", [getattr(d, aspect) for d in stated], dtype=pl.Date)
            for aspect in history.ASPECTS
        ),
        pl.Series("enacted_year", [d.enacted_year for d in stated], dtype=pl.Int64),
    ]


def law_versions(
    found: Ledgers,
    observations: pl.DataFrame,
    jurisdiction: str,
    census: pl.DataFrame,
    conventions: Conventions = NONE,
) -> pl.DataFrame:
    """One row per distinct text of a section.

    Each row has the latest payload, the dates of the latest history note,
    the latest placement, the ledger columns, and the next version.
    """
    if found.payload is None or not observations.height:
        return empty(VERSION_SCHEMA)
    placed = ledger.fold([observations], VERSION_KEY, ("node_treepath",))
    assert placed is not None
    j, code, num = jurisdiction, pl.col("law_code"), pl.col("section_num")
    versions = placed.join(
        found.payload.select(*VERSION_KEY, *VERSION_PAYLOAD),
        on=list(VERSION_KEY),
        how="left",
        nulls_equal=True,
    ).with_columns(
        ids.law_version(j, code, num, pl.col("version_id"), pl.col("text_sha256")).alias("id"),
        ids.law_section(j, code, num).alias("section_id"),
        ids.law_node(j, code, pl.col("node_treepath")).alias("node_id"),
        pl.lit(j).alias("jurisdiction"),
        pl.col("effective_date").alias("effective_column"),
    )
    dated = versions.with_columns(stated_dates(versions, conventions)).with_columns(
        pl.coalesce("effective_date", "effective_column").alias("effective_date")
    )
    absent = ledger.with_absent_from(dated, census)
    return with_successors(absent).select(VERSION_SCHEMA.names()).cast(VERSION_SCHEMA).sort("id")


def law_history(found: Ledgers, jurisdiction: str, conventions: Conventions = NONE) -> pl.DataFrame:
    """One row per wording of a version's history note, with the dates it states."""
    if found.histories is None:
        return empty(HISTORY_SCHEMA)
    j, code, num = jurisdiction, pl.col("law_code"), pl.col("section_num")
    version = ids.law_version(j, code, num, pl.col("version_id"), pl.col("text_sha256"))
    note_sha = pl.col("history").map_elements(
        lambda h: hashlib.sha256(h.encode()).hexdigest(), return_dtype=pl.String
    )
    notes = found.histories.with_columns(
        ids.law_history(version, note_sha).alias("id"),
        version.alias("law_version_id"),
        ids.law_section(j, code, num).alias("section_id"),
        pl.lit(j).alias("jurisdiction"),
    )
    return (
        notes.with_columns(stated_dates(notes, conventions))
        .select(HISTORY_SCHEMA.names())
        .cast(HISTORY_SCHEMA)
        .sort("law_version_id", "first_seen", "id")
    )


def law_notes(found: Ledgers, jurisdiction: str) -> pl.DataFrame:
    """One row per note printed with a section or a heading."""
    if found.notes is None:
        return empty(NOTE_SCHEMA)
    j, code = jurisdiction, pl.col("law_code")
    target = (
        pl.when(pl.col("section_num").is_not_null())
        .then(ids.law_section(j, code, pl.col("section_num")))
        .otherwise(ids.law_node(j, code, pl.col("node_treepath")))
    )
    note_sha = pl.concat_str(pl.col("kind"), pl.lit("\0"), pl.col("text")).map_elements(
        lambda t: hashlib.sha256(t.encode()).hexdigest(), return_dtype=pl.String
    )
    return (
        found.notes.with_columns(target.alias("target_id"), pl.lit(j).alias("jurisdiction"))
        .with_columns(ids.law_note(pl.col("target_id"), note_sha).alias("id"))
        .select(NOTE_SCHEMA.names())
        .cast(NOTE_SCHEMA)
        .sort("target_id", "kind", "first_seen", "id")
    )


def with_successors(frame: pl.DataFrame) -> pl.DataFrame:
    """Add each version's successor, ordered by first_seen, then effective_date."""
    ordered = frame.sort(["section_id", "first_seen", "effective_date", "id"], nulls_last=False)
    return ordered.with_columns(
        pl.col("id").shift(-1).over("section_id").alias("successor_id"),
        pl.col("effective_date").shift(-1).over("section_id").alias("successor_effective_date"),
    )


def law_nodes(found: Ledgers, jurisdiction: str, census: pl.DataFrame) -> pl.DataFrame:
    if found.nodes is None:
        return empty(NODE_SCHEMA)
    j, code = jurisdiction, pl.col("law_code")
    with_ids = found.nodes.with_columns(
        ids.law_node(j, code, pl.col("node_treepath")).alias("id"),
        ids.law_node(j, code, pl.col("parent_treepath")).alias("parent_id"),
        pl.lit(j).alias("jurisdiction"),
    )
    return ledger.with_absent_from(with_ids, census).select(NODE_SCHEMA.names()).cast(NODE_SCHEMA)


def law_ancestors(found: Ledgers, jurisdiction: str, census: pl.DataFrame) -> pl.DataFrame:
    if found.ancestors is None:
        return empty(ANCESTOR_SCHEMA)
    with_j = found.ancestors.with_columns(pl.lit(jurisdiction).alias("jurisdiction"))
    return ledger.with_absent_from(with_j, census).select(ANCESTOR_SCHEMA.names())


Parsed = Mapping[str, list[subdivide.Division]]


def parsed_versions(versions: pl.DataFrame) -> dict[str, list[subdivide.Division]]:
    """Each version's subdivisions, by version id: the tabulated ones where the
    cruncher gave them, else parsed from the text.
    """
    columns = ["id", "text", "line_indents", "subdivisions"]
    return {
        version_id: (
            subdivide.from_tabulated(tabulated)
            if tabulated is not None
            else subdivide.parse(text or "", indents)
        )
        for version_id, text, indents, tabulated in versions.select(columns).iter_rows()
    }


def law_subdivisions(
    versions: pl.DataFrame, parsed: Parsed, conventions: Conventions = NONE
) -> pl.DataFrame:
    """One row per subdivision, as a span into the version's text."""
    rows = []
    columns = ["id", "section_id", "jurisdiction", "drafting"]
    for version_id, section_id, jurisdiction, drafting in versions.select(columns).iter_rows():
        found = parsed[version_id]
        row_ids = [ids.compose(version_id, *d.path) for d in found]
        for d, row_id in zip(found, row_ids, strict=True):
            rows.append(
                (
                    row_id,
                    ids.compose(section_id, *d.path),
                    row_ids[d.parent] if d.parent is not None else None,
                    version_id,
                    section_id,
                    jurisdiction,
                    "/".join(d.path),
                    subdivide.level(conventions.levels.get(drafting or ""), d),
                    d.style,
                    d.number,
                    d.depth,
                    d.position,
                    d.sequence,
                    d.start,
                    d.end,
                )
            )
    return pl.DataFrame(rows, schema=SUBDIVISION_SCHEMA, orient="row")


def spaced(*parts: pl.Expr | str) -> pl.Expr:
    """Join the parts with single spaces, dropping nulls and blanks."""
    exprs = [pl.lit(p) if isinstance(p, str) else p.cast(pl.String).fill_null("") for p in parts]
    return pl.concat_str(exprs, separator=" ").str.replace_all(r"\s+", " ").str.strip_chars()


def section_blocks(versions: pl.DataFrame, nodes: pl.DataFrame) -> pl.DataFrame:
    """Versions as text blocks for segmenting.

    A block's context is the heading path above the version's latest node,
    then the section number and heading. block_sha256 hashes the context and
    the text. A stored block with the same hash keeps its segments.
    """
    latest = nodes.sort("last_seen", "last_publication").unique("id", keep="last")
    heading = dict(
        latest.select("id", pl.col("heading").str.replace(HEADING_RANGE, "")).iter_rows()
    )
    parent = dict(latest.select("id", "parent_id").iter_rows())

    def ancestry(node_id: str | None) -> str | None:
        found: list[str] = []
        seen: set[str] = set()
        while node_id is not None and node_id not in seen:
            seen.add(node_id)
            if heading.get(node_id):
                found.append(heading[node_id])
            node_id = parent.get(node_id)
        return CONTEXT_SEP.join(reversed(found)) or None

    section = spaced(pl.col("law_code"), pl.col("section_num"), pl.col("heading"))
    path = pl.Series("ancestry", [ancestry(n) for n in versions["node_id"]], dtype=pl.String)
    blocks = versions.with_columns(path).select(
        pl.col("id").alias("block_id"),
        pl.lit(registry.LAW_SECTION.kind).alias("block_kind"),
        pl.when(pl.col("ancestry").is_null())
        .then(section)
        .otherwise(pl.concat_str(pl.col("ancestry"), pl.lit(CONTEXT_SEP), section))
        .alias("context"),
        "text",
        pl.col("line_indents").alias("indents"),
        "drafting",
    )
    digests = [
        block_sha256(context, text)
        for context, text in blocks.select("context", "text").iter_rows()
    ]
    return blocks.with_columns(pl.Series("block_sha256", digests, dtype=pl.String))


def block_sha256(context: str | None, text: str | None) -> str:
    return hashlib.sha256(f"{context or ''}\0{text or ''}".encode()).hexdigest()


def segments(
    blocks: pl.DataFrame,
    jurisdiction: str,
    settings: segment.Settings = segment.DEFAULT,
    parsed: Parsed | None = None,
) -> pl.DataFrame:
    """One row per segment, as a span into its block's text.

    content_sha256 hashes the segment's context and text, so the same text
    in two jurisdictions has the same hash.
    """
    known = parsed or {}
    rows = []
    columns = ("block_id", "block_kind", "context", "text", "indents", "block_sha256")
    for block_id, kind, headings, text, indents, digest in blocks.select(columns).iter_rows():
        pieces = segment.cut(text or "", settings.limit, indents, known.get(block_id))
        for ordinal, p in enumerate(pieces):
            body = (text or "")[p.start : p.end]
            context = segment.context(headings, p, positions=settings.positions)
            rows.append(
                (
                    ids.compose(block_id, "seg", str(ordinal)),
                    block_id,
                    kind,
                    jurisdiction,
                    ordinal,
                    p.start,
                    p.end,
                    p.end - p.start,
                    "/".join(p.path),
                    p.position,
                    p.siblings,
                    p.kind,
                    p.piece,
                    context,
                    segment.content_sha256(context, body),
                    digest,
                )
            )
    return pl.DataFrame(rows, schema=SEGMENT_SCHEMA, orient="row")


@dataclass(frozen=True)
class Reuse:
    """Stored rows a fold keeps: subdivisions of known versions, and segments
    of blocks whose hash is unchanged.
    """

    subdivisions: pl.DataFrame | None = None
    segments: pl.DataFrame | None = None


NO_REUSE = Reuse()


def carries_law(found: Ledgers) -> bool:
    return found.payload is not None or found.nodes is not None


def datasets(
    found: Ledgers,
    jurisdiction: str,
    settings: segment.Settings = segment.DEFAULT,
    reuse: Reuse = NO_REUSE,
    conventions: Conventions = NONE,
) -> dict[str, pl.DataFrame]:
    """Every dataset of the jurisdiction.

    The law datasets are left out if no publication carries law tables.
    """
    pubs = publications(found)
    common = {
        registry.PUBLICATIONS: pubs,
        registry.SESSIONS: sessions(found, jurisdiction),
        registry.LAW_CODES: _kept(found.codes, pubs, CODES_SCHEMA, "law_code"),
        registry.LAW_ATTACHMENTS: _kept(found.attachments, pubs, ATTACHMENTS_SCHEMA, "code"),
    }
    if not carries_law(found):
        return common
    census = ledger.censuses(pubs)
    observations = law_observations(found, jurisdiction, census)
    versions = law_versions(found, observations, jurisdiction, census, conventions)
    nodes = law_nodes(found, jurisdiction, census)

    kept_subdivisions = empty(SUBDIVISION_SCHEMA)
    todo = versions
    if reuse.subdivisions is not None:
        kept_subdivisions = reuse.subdivisions.filter(
            pl.col("law_version_id").is_in(versions["id"].implode())
        )
        todo = versions.filter(~pl.col("id").is_in(kept_subdivisions["law_version_id"].implode()))
    parsed = parsed_versions(todo)
    subdivisions = pl.concat([kept_subdivisions, law_subdivisions(todo, parsed, conventions)]).sort(
        "law_version_id", "sequence"
    )

    blocks = section_blocks(versions, nodes)
    kept_segments = empty(SEGMENT_SCHEMA)
    if reuse.segments is not None:
        kept_segments = reuse.segments.filter(
            pl.col("block_sha256").is_in(blocks["block_sha256"].implode())
            & pl.col("block_id").is_in(blocks["block_id"].implode())
        )
        blocks = blocks.filter(~pl.col("block_id").is_in(kept_segments["block_id"].implode()))
    cut = segments(blocks, jurisdiction, settings, parsed)
    all_segments = pl.concat([kept_segments, cut]).sort("block_id", "ordinal")

    return {
        **common,
        registry.LAW_OBSERVATIONS: observations,
        registry.LAW_VERSIONS: versions,
        registry.LAW_HISTORY: law_history(found, jurisdiction, conventions),
        registry.LAW_NODES: nodes,
        registry.LAW_ANCESTORS: law_ancestors(found, jurisdiction, census),
        registry.LAW_SUBDIVISIONS: subdivisions,
        registry.SEGMENTS: all_segments,
        registry.LAW_NOTES: law_notes(found, jurisdiction),
    }
