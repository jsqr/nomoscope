# Agents

See [README.md](README.md) for the library, [DATA.md](DATA.md) for the
tables and dates, and [CONTRIBUTING.md](CONTRIBUTING.md) for commit and AI
policy.

nomoscope is generic and public. It doesn't contain code for publishers,
jurisdictions, project specs or results. Those belong in project repos.

## Environment

- Dependencies are managed with `uv`, tasks with `just`.
- Checks: `just check`, `just typecheck`, `just test`. `just fmt` formats.

## Layout

| Path | Contents |
| --- | --- |
| `src/nomoscope/{errors,parquet,partition,providers,settings}.py` | Errors, parquet writes, partition paths, model providers, settings |
| `src/nomoscope/chat.py` | Chat models and a completion that returns JSON fitting a schema |
| `src/nomoscope/graph/` | The graph store: records, steps, and staleness |
| `src/nomoscope/snarf/` | Sources, feeds, fetching, and local import |
| `src/nomoscope/pdf/` | A PDF's text layer, outline, and OCR, read as lines with layout features |
| `src/nomoscope/crunch/` | The registry, routing, the tabulated schemas, and the runner |
| `src/nomoscope/derive/` | The ledger, the datasets, conventions, parsers, segments, and the build step |
| `src/nomoscope/embed/` | Embedding and reranking models, the provider client, the vector store |
| `src/nomoscope/extract/` | Extraction tasks, their units, store, run, resolvers, and the build step |
| `src/nomoscope/catalog/` | The DuckDB catalog |
| `src/nomoscope/search/` | Search: vectors, full text, fusion, reranking |
| `src/nomoscope/answer/` | Answers from retrieved passages; question groups that form a DAG |
| `src/nomoscope/cli/`, `status.py`, `show.py` | The command, built with `cli.app(registry, name)` |
| `tests/fake.py` | A fake source and cruncher for the tests |

Packages import only from their own layer or below: the bottom modules,
then `graph`, `snarf`, `pdf`, `crunch`, `derive`, `embed` and `extract`, `catalog`,
`search`, `answer`, and the command modules. `tests/test_layering.py` checks this.

## Rules by stage

**snarf** stores files as received, unpacked from containers, deduplicated
by hash, with provenance. `sources/snarfed/` is append-only. snarf does
not read file contents. Registration reads only `fetch.json`.

**crunch** can do whatever a source needs. Its output must be
reproducible from `sources/snarfed/`. Every cruncher writes the tabulated
tables with `runner.write_tabulated`, which calls `conform`. Each tabulated
record has a tabulate step keyed `(tabulate, <cruncher>, <record>)` and one
`law_publications` row. The routing table chooses the cruncher.

**derive** runs the same code for every jurisdiction, source, and
cruncher. It reads only the tabulated tables and the config tables. Anything
specific to a source or code is data, not a branch in code. Drafting
conventions are passed to the parsers as arguments. Dates stay in
separate columns. `law_in_force` combines them.

**catalog** holds only views and macros, uses paths relative to the data
root, and is rebuilt whole.

## What a project supplies

- A `Registry` of `Source`s and crunchers, passed to `cli.app(registry,
  name)`. A cruncher implements the `Cruncher` protocol, or is a `Tabulator`
  for one tabulate step per record. A project adds its own commands to the
  typer app that `cli.app` returns.
- In the same registry: the routes from records to crunchers, the segment
  settings, a directory of drafting conventions, the chat models it calls
  by slug, and the project's tables to show in the catalog.

`tests/fake.py` is the smallest example.

## Paid calls

`ocr run` sends every page of a PDF to an OCR model and is billed per
page. An agent runs it only with `--dry-run`, which prints the page and
request count, and leaves the paid run to a person. `embed --spend` and
`search --rerank` are billed per token; an agent runs `embed` without
`--spend`, which only estimates, searches without `--rerank`, and runs
`ask` and `extract` without `--spend`, unless a person approves the cost.
Tests never call a model.

## The graph store

Each step records its inputs, code version, and output tables. It reruns
when it has no row, an input's fingerprint changed, its code version
changed, or an output file is missing. Each stage module, `Source`, and
`Tabulator` has a `VERSION` or `version`. Bump it when the same inputs give
different output. Conventions and a cruncher's config files are inputs.

A crunch step's files are named by its fingerprint, and its manifest
(`crunch/manifests.py`) holds its graph rows. A data root adopts a step
from its manifest instead of running it, so the graph store can be
deleted and refilled (`graph reset`), and data roots can share crunched
files. Never rewrite a shared file in place: write a new name.

Tests must never run a real snarf against a developer's data root. The
tests use a scratch data root (`tests/conftest.py`).

## Git

Follow CONTRIBUTING.md. Agents commit only to local feature branches,
never to main. Agents never push, merge, or write pull request or issue
text.
