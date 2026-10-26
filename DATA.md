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
  graph.duckdb         the graph store
  catalog.duckdb       views over derived/
```

Each tier can be rebuilt from the one before it. The archive cannot be
rebuilt, because a source may stop serving a file. `staging/` is safe to
delete.

The graph store records each step's inputs, code version, and output
tables. A step reruns when one of these changes or an output file is
missing. `status` summarizes the store. `graph show` lists every step.

## Config root

The project's config tables are under `config_root`:

```
$config_root/
  crunchers.csv        which cruncher reads each record
  conventions/*.toml   drafting conventions
  jurisdictions.csv    the project's jurisdictions
  grammars/<source>/<jurisdiction>/<code>.toml   a code's grammar
```

Each file is optional. Each is an input of the steps that read it, so a
change reruns them.

## Stages

| stage | command | reads | writes |
| --- | --- | --- | --- |
| snarf | `snarf fetch`, `snarf due`, `snarf import` | a source, or local files | `sources/snarfed/` |
| crunch | `crunch` | `sources/snarfed/`, `crunchers.csv`, grammars | `sources/crunched/<cruncher>/` |
| derive | `derive` | carved tables, conventions, jurisdictions | `derived/`, the catalog |
| query | `sql "<query>"` | `catalog.duckdb` | nothing |

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

`snarf import` archives local files with a `file://` URL. The params
record the importer and the file's modification time. The modification
time is not used as the publication date. Importing the same bytes again
writes nothing.

## Registration and routing

Before crunching, each archived record is registered in the graph store
with its jurisdiction, code, session, and completeness. By default these
come from the record's params. The code is empty when the record carries
every code, as a state's bulk download does.

`crunchers.csv` then assigns each record a cruncher:

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

A cruncher turns records into the carved tables. It may write its own
snapshot tables first. Paths:

```
sources/crunched/<cruncher>/<table>/publication=<unit>/data.parquet          snapshots
sources/crunched/<cruncher>/carved/<table>/publication=<record>/data.parquet carved tables
```

### Grammared crunchers

A `GrammarCruncher` reads a printed code, such as a PDF or an HTML
export, with the code's grammar (`structure/grammar.py`). Each record has
two steps:

- `parse` writes snapshot tables: `parse_lines` (every line with its
  layout), `parse_nodes` (the tree, with the lines each node spans), and
  `parse_findings` (what the checks report). The grammar file is an input,
  so editing it reruns the parse.
- `carve` writes the carved tables from the snapshots.

A grammar's `[review]` records whether a person has checked its parse, and
how many findings of each check were accepted. A carve is held, and derive
does not read it, until the review is `reviewed` and no check has more
findings than it accepted. `status` counts held carves. `grammar sample`
prints a record's lines for drafting a grammar, and `grammar infer`
drafts one with a chat model (billed; the `infer` extra). `grammar check`
parses a record with a grammar and writes nothing.

### The carved tables

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

Derive combines the carved tables of every publication of a jurisdiction,
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
codes (`state_fips`, `county_fips`, `place_fips`, `geoid`). nomoscope
includes the country, the states, and DC. A project adds its own in
`jurisdictions.csv` in the config root.

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
| `effective_date` | the history note, else the source's own effective date (`effective_column`) |
| `operative_date` | the history note |
| `inoperative_date` | the history note: when the text expires |
| `repeal_date` | the history note |
| `statutory_default_date` | the carved column |
| `first_seen`, `last_seen`, `absent_from` | the publications |

The catalog's `law_in_force` gives each version's interval in force:

- It starts at the operative date, else the effective date, else
  `first_seen`.
- It ends at the repeal date, else the inoperative date, else the
  successor's start, else `absent_from`.
- `start_basis` and `end_basis` name the column each end came from. An
  open interval has `end_basis` `current`.

### Drafting conventions

Drafting conventions give the date phrases in history notes and the
words for subdivision levels. They belong to a codifier's format, which
can cover many jurisdictions. Each set is a file,
`conventions/<drafting>.toml`, in the config root.
`src/nomoscope/derive/conventions.py` gives the format. A section with no
conventions has no stated dates and no level words. Any change to the
conventions rebuilds every jurisdiction.

### Subdivisions and segments

`law_subdivisions` finds the subdivisions in each version's text, such as
(a), (1), (A), and (i), as spans into the text. The rules are in
`src/nomoscope/derive/subdivide.py`.

`segments` cuts each version into spans for retrieval. A version within
the limit is one segment. A longer version is cut into subdivisions, and
long leaves into pieces. The rules are in `src/nomoscope/derive/segment.py`.
A segment's `context` is the heading path, the section number and heading,
and the lead-in text of each enclosing subdivision. `content_sha256`
hashes the context and the text, so the same text in two jurisdictions
has the same hash. The limit is set in `[segments]` in the settings file.

## The catalog

`catalog.duckdb` holds views, not data: one per derived dataset, plus
`law_in_force` and four macros. It is rebuilt after each derive, or with
`catalog`. The views use paths relative to the data root, so the data
root can move.

| name | returns |
| --- | --- |
| `law_in_force` | every version, with its interval in force and the basis of each end |
| `law_at(jurisdiction, date)` | the versions in force on a date, with text and placement |
| `law_placed_at(jurisdiction, date)` | each version's placement on a date |
| `session_end(jurisdiction, session)` | the session's stated end, else its last publication's date |
| `law_at_session(jurisdiction, session)` | the versions in force at a session's end |

```
myproject sql "SELECT section_num, start_basis, end_basis
               FROM law_at('us-il-chicago', DATE '2024-07-01')"
myproject sql "SELECT * FROM publications" --format csv
```

In Python, `nomoscope.catalog.build.connect(settings)` opens the catalog
read-only. A plain `duckdb.connect` must first run
`SET file_search_path = '<data_root>'`.
