# nomoscope

nomoscope is a library that organizes legal authorities, tracks changes,
and enables systematic queries and processing. It fetches or imports
each publication of a code and carves it into tables. For each section
it records every distinct text, the publications that carried it, the
dates its history states, and when it was in force. The tables are parquet
files, queried through DuckDB or polars.

A project that uses nomoscope should supply:

- **Sources and crunchers:** code that fetches a publisher's files and
  turns them into the carved tables, passed to nomoscope in a `Registry`.
- **Config tables:** which cruncher reads each code, the drafting
  conventions, and the project's jurisdictions.
- **A data root:** a directory outside the repository for the archive and
  the tables.

[DATA.md](DATA.md) describes the tables, ids, and dates.

## Setup

Developed on macOS and Linux. Requires Python 3.14,
[uv](https://docs.astral.sh/uv/), and [just](https://just.systems/).

```bash
just setup       # uv sync
just check       # ruff
just typecheck   # basedpyright
just test        # pytest
```

## Using nomoscope from a project

A project constructs its command-line interface from its registry:

```python
# myproject/cli.py
from nomoscope import Carver, Registry, cli

from myproject import publisher

REGISTRY = Registry(
    sources={"publisher": publisher.SOURCE},
    crunchers={"springfield-municipal": Carver(publisher.carve_municipal, "1")},
)
app = cli.app(REGISTRY, "myproject")
```

With `myproject = "myproject.cli:app"` under `[project.scripts]`, the
command reads its settings from the first of `$MYPROJECT_CONFIG`,
`myproject.toml` in the working directory or a parent, and
`~/.config/myproject/config.toml`:

```toml
data_root = "~/data/myproject"   # required. MYPROJECT_DATA_ROOT overrides it.
config_root = "config"           # relative to this file
user_agent = "myproject/0.1 (+mailto:team@example.org)"

[sources.publisher]
key_env = "PUBLISHER_API_KEY"    # read with Settings.secret("publisher")

[segments]
limit = 300                      # words, or tokens with unit = "tokens"
```

Commands:

```bash
myproject snarf due                      # fetch the feeds due today
myproject snarf import publisher FILE -f FEED -p jurisdiction=us-il-springfield
myproject crunch                         # carve new records
myproject derive                         # build the derived tables
myproject status                         # show what has run
myproject sql "SELECT section_num, text FROM law_at('us-il-springfield', DATE '2024-07-01')"
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md).
