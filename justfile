# Optional shortcuts; the application only needs uv.
check: lint typecheck test

setup:
    uv sync --locked --no-install-project
    uv sync --locked --no-build-isolation

lint: setup
    if command -v ruff >/dev/null 2>&1; then ruff check .; else uv tool run --from ruff==0.16.10 ruff check .; fi
    if command -v ruff >/dev/null 2>&1; then ruff format --check .; else uv tool run --from ruff==0.16.10 ruff format --check .; fi

typecheck: setup
    uv run --locked mypy --config-file pyproject.toml --no-incremental

test: setup
    uv run --locked pytest

build: setup
    uv build --python .venv/bin/python --no-sources --no-build-isolation

# Background Streamable HTTP server; no client-specific dependency.
start:
    uv run --locked python -m poelis_mcp start

stop:
    uv run --locked python -m poelis_mcp stop

status:
    uv run --locked python -m poelis_mcp status
