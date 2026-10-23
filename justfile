# List the recipes.
default:
    @just --list --unsorted

# Install the package and the dev tools.
[group('setup')]
setup:
    uv sync

# Lint and check formatting. Run before committing.
[group('dev')]
check:
    uv run ruff check .
    uv run ruff format --check .

# Apply formatting and safe lint fixes.
[group('dev')]
fmt:
    uv run ruff format .
    uv run ruff check --fix .

[group('dev')]
typecheck:
    uv run basedpyright

[group('dev')]
test *ARGS:
    uv run pytest {{ARGS}}
