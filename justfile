# Run all checks by default.
check: lint typecheck test

[private]
_environment:
    @test "${POELIS_DEV_SHELL:-}" = 1 || { printf '%s\n' 'Use nix develop -c just <recipe>, or enable direnv.' >&2; exit 1; }

setup: _environment
    uv sync --locked --no-install-project
    uv sync --locked --no-build-isolation

lint: setup
    ruff check .
    ruff format --check .

typecheck: setup
    uv run --locked mypy --config-file pyproject.toml --no-incremental

test: setup
    uv run --locked pytest

build: setup
    uv build --python .venv/bin/python --no-sources --no-build-isolation
