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
| `src/nomoscope/{errors,parquet,partition,settings}.py` | Errors, parquet writes, partition paths, settings |
| `src/nomoscope/graph/` | The graph store: records, steps, and staleness |
| `src/nomoscope/snarf/` | Sources, feeds, fetching, and local import |
| `src/nomoscope/structure/` | Lines, bookmarks, grammars, the parser, and checks for printed codes |
| `src/nomoscope/crunch/` | The registry, routing, the tabulated schemas, the runner, and the grammared cruncher |
| `src/nomoscope/derive/` | The ledger, the datasets, conventions, parsers, segments, and the build step |
| `src/nomoscope/catalog/` | The DuckDB catalog |
| `src/nomoscope/cli/`, `status.py`, `show.py` | The command, built with `cli.app(registry, name)` |
| `tests/fake.py` | A fake source and cruncher for the tests |

Packages import only from their own layer or below: the bottom modules,
then `graph`, `snarf`, `structure`, `crunch`, `derive`, `catalog`, and the command
modules. `tests/test_layering.py` checks this.

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
  name)`. A cruncher implements the `Cruncher` protocol, or is a `Tabulator`,
  or a `GrammarCruncher` with a reader for its format and a directory of
  grammars.
- In the same registry: the routes from records to crunchers, the segment
  settings, and a directory of drafting conventions.

`tests/fake.py` is the smallest example.

## Paid calls

`grammar infer` sends a page sample to a chat model and is billed per
token. `ocr run` sends every page of a PDF to an OCR model and is billed
per page. An agent runs these only with `--dry-run`, which prints the page and
token count, and leaves the paid run to a person. Tests never call a model.

## The graph store

Each step records its inputs, code version, and output tables. It reruns
when it has no row, an input's fingerprint changed, its code version
changed, or an output file is missing. Each stage module, `Source`, and
`Tabulator` has a `VERSION` or `version`. Bump it when the same inputs give
different output. Grammars and conventions are inputs.

Tests must never run a real snarf against a developer's data root. The
tests use a scratch data root (`tests/conftest.py`).

## Git

Follow CONTRIBUTING.md. Agents commit only to local feature branches,
never to main. Agents never push, merge, or write pull request or issue
text.
