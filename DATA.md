# The data

This file describes where nomoscope stores data, what each stage writes,
and how dates are kept. The column schemas are in
`src/nomoscope/crunch/schemas.py` and `src/nomoscope/derive/datasets.py`.

## Data root

All data is under the project's data root, `data_root` in its settings
file:

```
$data_root/
  sources/snarfed/     the archive of fetched and imported files
  sources/crunched/    tables built from the archive, by cruncher
  derived/             tables in the shared layout
  staging/             downloads in progress
  coverage/            the jurisdictions the project covers, from `coverage`
  embeddings/          vectors of segments, by content hash, from `embed`
  graph.duckdb         the graph store
  catalog.duckdb       views over derived/, coverage/, embeddings/, and the project's tables
  search.duckdb        the full-text index of segments, rebuilt by `search --text`
```

A project may keep its own directories under the data root, such as
records of people's decisions; `Registry.views` shows their tables in the
catalog.

Each tier can be rebuilt from the one before it. The archive cannot be
rebuilt, because a source may stop serving a file. `coverage/` cannot be
rebuilt either, because it records people's decisions. `embeddings/`
can be rebuilt only by paying for the vectors again. `staging/` and
`search.duckdb` are safe to delete.

The graph store records each step's inputs, code version, and output
tables. A step reruns when one of these changes or an output file is
missing. `status` summarizes the store. `graph show` lists every step.

### Sharing a data root

Several data roots can share one copy of the archive, the crunched
files, and the people's decisions, because none of these files is ever
rewritten: a file name always holds the same content. The graph store,
the catalog, `derived/`, and `staging/` stay with each data root.

- The archive stores files by hash and each record under a new name. A
  record counts once its `fetch.json` exists.
- A crunch step names its files by its fingerprint and writes a step
  manifest last (see Crunched). A step counts once its manifest exists.
- `coverage/` and a project's decision tables write one new file per
  change, named by time and writer.
- `embeddings/` writes one new file per run, named by time and writer.

Copying these tiers in two passes, files first and then the `fetch.json`
files and step manifests, never leaves a record or step that looks
finished with files missing. `graph reset` deletes the graph store; the
next crunch registers the records again and adopts each finished step.

### Coverage

`coverage/` lists the jurisdictions the project collects codes for. Each
row gives a jurisdiction, whether it is listed or removed, its code's
publisher and URL, who wrote the row and when, and a note. A publisher is
a source id when the project has a source for it, and otherwise a name.
Files are only added. The latest row of a jurisdiction is the one in
force, so the table keeps every change.

```
myproject coverage set us-il-chicago --publisher example --url https://example.org/chicago
myproject coverage remove us-il-chicago --note "code withdrawn"
myproject coverage --state il
```

`coverage set` refuses a jurisdiction that is not in the jurisdictions
table, and keeps the values it is not given. `coverage` prints the
jurisdictions listed, with the first and latest fetch of each one's code,
read from the archive. In Python, `nomoscope.coverage.write(settings,
entries, by)` writes many rows as one file.

A project can give listings, the codes its publishers' libraries list,
as `Registry.listings`: a function from settings to
`nomoscope.coverage.Listing` values, each with a state, the name as the
library writes it, a publisher, a URL, and the archive record it came
from. `coverage match` pairs them with listed jurisdictions that have no
publisher yet. A listing fits a jurisdiction of its state when its name,
with a kind such as "City of" or "County" and a trailing ", ST" removed,
is the jurisdiction's Census name, and its kind, if the name or the
listing gives one, shares a word with the Census kind. A jurisdiction with exactly one fitting
listing, which fits no other, gets that listing's publisher and URL. The
others are printed to settle with `coverage set`. `--dry-run` writes
nothing.

## The project's registry

The machine's settings file gives where the data root is, the user agent,
and which environment variables hold keys. The project's committed
settings file (`Registry.settings`, read under the machine's) gives
choices such as a stage's model and the OCR model. A setting that changes
a step's output must enter that step's code version or inputs, so that
changing it reruns the step. Everything else that decides what the
project builds is in the registry it passes to `cli.app`:

- its sources and crunchers;
- its routes, which send each record to a cruncher;
- its segment settings, which derive cuts text with;
- its directory of drafting conventions, `<drafting>.toml` files;
- a reader of publishers' listings, for `coverage match`;
- its own tables to show in the catalog (`views`);
- its committed settings file and the tables it reads (`settings`, `tables`).

Conventions, and any config files a cruncher declares, are inputs of the
steps that read them, so a change reruns those steps.

## Stages

| stage | command | reads | writes |
| --- | --- | --- | --- |
| snarf | `snarf fetch`, `snarf due`, `snarf import` | a source, or local files | `sources/snarfed/` |
| crunch | `crunch` | `sources/snarfed/`, routes, a cruncher's config | `sources/crunched/<cruncher>/` |
| derive | `derive` | tabulated tables, conventions, jurisdictions | `derived/`, the catalog |
| embed | `embed --spend <dollars>` | `derived/segments`, the block tables | `embeddings/` |
| query | `sql "<query>"`, `cite`, `search` | `catalog.duckdb`, `embeddings/` | `search.duckdb` |

## Snarfed

Each fetch or import writes a record: a directory named by the time and
the first eight hex digits of the file's hash.

```
sources/snarfed/<source>/2026/2026-09-06T161743Z-830a8445/
  fetch.json        the URL, the parameters, and the server's response headers
  manifest.jsonl    one line per file: name, hash, size
```

The files are stored once each, by hash, in
`sources/snarfed/blobs/sha256/<aa>/<hash>`. Zip files are unpacked on
import.

A record can also hold a tree of files. Its manifest lists every file,
and its hash is the manifest's hash.

A record can have attached records (`snarf/attach.py`): files made from it,
such as OCR responses for a PDF, archived because they cannot be made
again for free. An attached record's params name its record (`of`) and
repeat that record's params; its feed is not crunchable, so it is never
routed to a cruncher. A cruncher can read it with its record and declare it
an input, so attaching one reruns that record's steps.

`ocr run <source> <record>` OCRs a record's PDF in segments of pages
(billed; the `ocr` extra; `--dry-run` shows pages and requests) and
attaches the responses (feed `ocr`). `ocr import` attaches responses made
elsewhere for the same file. `nomoscope.pdf.lines` reads a PDF as lines
with layout features, taking a page's text from its OCR where the text
layer is unreadable, and each table from its OCR `table` block, as
markdown rows.

`snarf import` archives local files with a `file://` URL. The params
record the importer and the file's modification time. The modification
time is not used as the publication date. Importing the same bytes again
writes nothing.

## Registration and routing

Before crunching, each archived record is registered in the graph store
with its jurisdiction, code, session, and completeness. By default these
come from the record's params. The code is empty when the record carries
every code, as a state's bulk download does.

The registry's routes then assign each record a cruncher:

| source | jurisdiction | law_code | cruncher | valid_from |
| --- | --- | --- | --- | --- |
| publisher | us-il-springfield | municipal | springfield-municipal | |
| publisher | us-il-springfield | municipal | springfield-municipal-2 | 2023-06-01 |
| state | us-ca | * | ca-dump | |

- `*` matches any value. An empty code matches only `*`.
- `valid_from` is compared with the record's date: its last-modified time,
  else its fetch time.
- The route that names the most fields wins, then the latest `valid_from`.
- A record with no matching route is not crunched. `status` counts it as
  unrouted.

## Crunched

A cruncher turns records into the tabulated tables. It may write its own
snapshot tables first. Paths:

```
sources/crunched/<cruncher>/<table>/publication=<unit>/<fingerprint>.parquet           snapshots
sources/crunched/<cruncher>/tabulated/<table>/publication=<record>/<fingerprint>.parquet tabulated tables
sources/crunched/<cruncher>/steps/<stage>/<unit>/<fingerprint>.json                    step manifests
```

`<fingerprint>` is the first 16 hex digits of the step's fingerprint, so
a step rerun under other inputs or code writes beside its old files.
Readers take a step's paths from the graph store, not from a directory
listing.

After its tables, a step writes its manifest: what the graph store holds
for it (inputs, code version, details, and each table's path, rows, and
column fingerprint). When crunch finds a step to run whose manifest and
tables are already present, it records the step from the manifest and
does not run it; the crunch output marks it "adopted". `--force` and
`--record` always run.

A tabulation can be held: a cruncher that records `admitted: false` in
the tabulate step's details keeps derive from reading it, for instance
until a person has checked it. `status` counts held tabulations.

### The tabulated tables

| table | one row per |
| --- | --- |
| `law_sections` | section in the publication |
| `law_nodes` | heading in the publication |
| `law_publications` | publication (one row) |
| `sessions` | legislative session (optional) |

Every row has a `jurisdiction`. `law_code` is the code's short name in
the jurisdiction, such as `municipal` or `GOV`. `drafting` names the
section's drafting conventions. `statutory_default_date` is set where the
source has a default effective-date rule.

Section text is normalized before it is hashed into `text_sha256`. Line
endings, Unicode composition, quote marks, and spacing are made uniform,
and soft hyphens are removed. Other characters, such as the section sign,
are kept. The number of lines does not change. So the same words with different
spacing are one version. The source's own form belongs in `markup`.

A cruncher that knows a section's subdivisions gives them in
`subdivisions`: each one's path below the section (`A/1`), the source's
word for its level, its style and number, and its span into the
normalized text.

`law_publications` describes the publication:

- `published_date` and `published_basis`: the stated date (`stated`),
  else the server's last-modified time (`last_modified`), else the fetch
  time (`fetched`).
- `completeness`: `full` if the publication contains every section of the
  codes in `law_codes`, `partial` if it contains only changes.
- `edition`, such as "Supp. No. 47", if stated.
- `currency_text` and `currency_date`, such as "current through Ord.
  2025-31, passed March 4, 2025", if stated.
- `session`, if any.

## Derived

Derive combines the tabulated tables of every publication of a jurisdiction,
from every source:

```
derived/<dataset>/jurisdiction=<j>/data.parquet
derived/jurisdictions/data.parquet
```

There is no time partition. A derived row is one distinct thing, such as
one text of a section, with the publications it appeared in. The law on a
given date is a query (see "The catalog").

### Ids

Jurisdiction ids are ISO 3166-2 codes in lower case, extended with `-`:
`us`, `us-il`, `us-il-chicago`, `us-ca-los_angeles_county`. The
`jurisdictions` dataset gives each one's name, kind, parent, and Census
codes (`state_fips`, `county_fips`, `place_fips`, `geoid`).

The dataset is built from the Census Bureau's files, which a project
fetches through the `census` source (`snarf/census.py`):

```
snarf fetch census -f states -f counties -f places -f cousubs -p year=2024
```

It holds the country, the states and territories, every county, and the
places and county subdivisions with a functioning government; statistical
areas such as CDPs are left out. `derive/census.py` gives the rules for
ids, including names that two governments in a state share.
`jurisdictions find <name> --state <st>` prints the ids that match a name.
Without Census records the dataset is not built. With them, derive stops
if a registered record names a jurisdiction the dataset lacks. Ids must
not change once records use them, and this check catches a Census year
that would change one.

Other ids join the jurisdiction, a kind, and the source's keys with `/`:

| id | names |
| --- | --- |
| `us-il-chicago/law/municipal/4-60-010` | a section |
| `us-il-chicago/law/municipal/4-60-010/3f2a9c1d07e4` | a version: the first 12 hex digits of its text's hash |
| `us-ca/law/GOV/65920./id_de728466/3f2a9c1d07e4` | a version, with the source's version id |
| `us-il-chicago/law/municipal/4-60-010/3f2a9c1d07e4/history/9b1e0c44d2a7` | a wording of a version's history note |
| `us-il-chicago/node/municipal/4.60` | a heading |
| `us-il-chicago/law/municipal/4-60-010/3f2a9c1d07e4/b/1` | subdivision (b)(1) of a version |

### The datasets

| dataset | one row per |
| --- | --- |
| `publications` | publication |
| `sessions` | session |
| `law_observations` | text of a section at one place in the code |
| `law_versions` | distinct text of a section |
| `law_history` | wording of a version's history note |
| `law_nodes` | wording of a heading at one place in the code |
| `law_ancestors` | pair of a section or heading and one of its ancestors |
| `law_subdivisions` | subdivision of a version |
| `segments` | segment of a version |

`publications` and `sessions` are built for every jurisdiction. The law
datasets are built only if the jurisdiction's publications carry law
tables.

### Observation columns

Each observed dataset has these columns:

- `first_seen` and `last_seen`: the dates of the first and last
  publications that carried the row.
- `last_full_seen`: the date of the last full publication that carried it.
- `observations`: the number of publications that carried it.
- `first_publication` and `last_publication`: their ids.
- `absent_from`: the date of the first full publication of the row's code
  after the row was last seen. Null while the row is in the latest full
  publication. Partial publications do not set it. `law_history` does not
  have this column.

Publications can be combined in any order. Adding a publication to the
stored datasets gives the same result as rebuilding from all
publications. `derive` adds new publications when nothing else changed,
and rebuilds otherwise.

`law_observations` is keyed by a section's text and its node. A section
moved to another chapter is one version with two placements.
`law_versions` keeps the latest placement. `law_at` uses the placement on
its date.

A version is identified by its text. If only the history note changes,
the version stays the same and `law_history` gets a second row. Each
wording keeps its own dates. `law_versions` has the dates of the latest
note.

### Dates

A version keeps each date in its own column:

| column | source |
| --- | --- |
| `enacted_date` | the history note: when the act was passed |
| `enacted_year` | the year of `enacted_date`, else of a note that gives only a year |
| `effective_date` | the history note, else the source's own effective date (`effective_column`) |
| `operative_date` | the history note |
| `inoperative_date` | the history note: when the text expires |
| `repeal_date` | the history note |
| `statutory_default_date` | the tabulated column |
| `first_seen`, `last_seen`, `absent_from` | the publications |

The catalog's `law_in_force` gives each version's interval in force:

- It starts at the operative date, else the effective date, else
  `first_seen`.
- It ends at the repeal date, else the inoperative date, else the
  successor's start, else `absent_from`.

A version's history note is its own notes. A code that notes a chapter's
adoption once, above its sections, can have its cruncher give a section
without notes its nearest division's notes in `division_history`; its
dates then come from those.
- `start_basis` and `end_basis` name the column each end came from. An
  open interval has `end_basis` `current`.

### Drafting conventions

Drafting conventions give the date phrases in history notes and the
words for subdivision levels. They belong to a codifier's format, which
can cover many jurisdictions. Each set is a file, `<drafting>.toml`, in
the conventions directory the registry names.
`src/nomoscope/derive/conventions.py` gives the format. A section with no
conventions has no stated dates and no level words. Any change to the
conventions rebuilds every jurisdiction.

### Subdivisions and segments

`law_subdivisions` has one row per subdivision of a version, as a span
into its text. A version whose tabulated section gives `subdivisions` uses
them. Otherwise derive finds the parenthesized enumerators, such as (a),
(1), (A), and (i), with the rules in `src/nomoscope/derive/subdivide.py`.
`law_versions` keeps the tabulated list, so a fold onto stored datasets
keeps it too.

`segments` cuts each version into spans for retrieval. A version within
the limit is one segment. A longer version is cut into subdivisions, and
long leaves into pieces. The rules are in `src/nomoscope/derive/segment.py`.
A segment's `context` is the heading path, the section number and heading,
and the lead-in text of each enclosing subdivision. `content_sha256`
hashes the context and the text, so the same text in two jurisdictions
has the same hash. The limit is in the registry's segment settings.

The text a segment stands for, the text that is embedded and indexed, is
`segment.content(context, body)`: the context, a blank line, and the body,
`text[start_char:end_char]` of its version (the body alone when there is
no context). `embed --check` rebuilds it for every segment and checks it
against `content_sha256`; the catalog's `segment_texts` view gives it in
SQL.

## Embeddings

`embed` embeds each distinct segment text not yet stored, with the model
the `[embeddings]` table names (default `qwen3-embedding-8b` at
`dimensions = 1024`; the key in `FIREWORKS_API_KEY`). It is billed per
token: without `--spend` it prints each jurisdiction's pending segments,
estimated tokens (characters / 4), and cost, and sends nothing. A run
takes segments in order until their estimated cost reaches the spend,
writes vectors every 20,000, so a run that stops keeps what it paid for,
and then rebuilds the catalog.

```
embeddings/model=<slug>-<dim>/jurisdiction=<j>/<time>-<writer>.parquet
  content_sha256    the segment's hash
  embedding         float32[dim], unit length
```

Qwen3-Embedding is a Matryoshka model: the provider returns the first
`dim` values of the full 4096, and `embed` normalizes them again.
Documents are embedded bare. A query carries an instruction in the
model's template, `Instruct: {instruction}\nQuery:{query}`. Vectors are
keyed by content, so a derive that changes segments changes only what is
pending, and a model or width names its own partition. Embedding is not
a graph step.

## Search

`search QUERY -j <jurisdiction> [--on DATE] [--text] [--rerank]` ranks
the segments of the sections in force on the date (default today):

1. by the inner product of their vectors with the query's, which carries
   the `[search] instruction`; the top `depth` (100);
2. with `--text`, by BM25 over their text, Porter-stemmed, from
   `search.duckdb`, which is rebuilt when derive changes the segments;
   `--text --no-vector` needs no vectors and no key;
3. with both lists, by reciprocal rank fusion, k = 60;
4. with `--rerank`, the top `rerank_depth` (50) by Qwen3-Reranker-8B's
   probability that the segment, as embedded, meets the query under the
   instruction (billed per token).

Each section gives one hit, its best segment: rank, citation, heading,
the segment's text as the snippet, and each list's score. In Python,
`nomoscope.search.query.search(settings, query, jurisdiction, ...)`
returns the hits.

| `[search]` key | default |
| --- | --- |
| `instruction` | Given a search query, retrieve the passages of laws and regulations that answer it |
| `depth` | 100 |
| `rerank_model` | `qwen3-reranker-8b` |
| `rerank_depth` | 50 |

## The catalog

`catalog.duckdb` holds views, not data: one per derived dataset, one per
table in the registry's `views`, `coverage` with the coverage rows in force,
`segment_texts` (each segment with its `body` and embedded `content`),
`segment_embeddings` (one stored vector per jurisdiction and hash, for the
`[embeddings]` model), plus `law_in_force` and five macros. It is rebuilt after each derive, or with
`catalog`. The views use paths relative to the data root, so the data
root can move.

| name | returns |
| --- | --- |
| `law_in_force` | every version, with its interval in force and the basis of each end |
| `law_at(jurisdiction, date)` | the versions in force on a date, with text and placement |
| `law_placed_at(jurisdiction, date)` | each version's placement on a date |
| `session_end(jurisdiction, session)` | the session's stated end, else its last publication's date |
| `law_at_session(jurisdiction, session)` | the versions in force at a session's end |
| `subdivision_at(jurisdiction, code, section, path, date)` | a section in force on a date, or one subdivision of it (`path` `'b/1'`), with its text |

```
myproject sql "SELECT section_num, start_basis, end_basis
               FROM law_at('us-il-chicago', DATE '2024-07-01')"
myproject sql "SELECT * FROM publications" --format csv
```

`cite` takes a citation as people write it, `67-14(A)(1)`:

```
myproject cite us-il-chicago municipal "67-14(A)(1)" --on 2024-07-01
```

In Python, `nomoscope.catalog.cite.lookup(settings, jurisdiction, code,
ref, on)` returns the same, and `nomoscope.catalog.build.connect(settings)` opens the catalog
read-only. A plain `duckdb.connect` must first run
`SET file_search_path = '<data_root>'`.
