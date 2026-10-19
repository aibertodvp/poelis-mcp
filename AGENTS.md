# Repository constraints

Read README.md before changing scope. Current state: **v0, staging only**,
with an HTTP MCP server, local test issuer and official Poelis SDK adapter.
Do not use production credentials or customer data; follow the README Getting started.

## Development environment

- Run `just check build` with uv 0.13.0 and just; uv manages Python 3.15.
- Application startup needs only uv: `uv run --locked python -m poelis_mcp start`.
  Keep operational code in the package, never dependent on `tests/` or just.
- Nix is optional: `nix develop --no-update-lock-file -c just check build` supplies
  the pinned toolchain. Just recipes must not require Nix or a shell marker.
- For optional direnv, review `.envrc` before allowing it; execute the just recipes.
- Preserve `flake.lock` and `uv.lock`; update dependencies deliberately, never during tests.
- `nix flake check` validates the shell, not Python tests. Execute the just recipes.
- Python annotations are mandatory; keep mypy strict, Pydantic validation and tests passing.

## Delivery and safety

- Changes require user review and approval.
- One writer per target; preserve unrelated work.
- Inspect official API/SDK contracts; never guess endpoints or compatibility.
- Separate caller authentication, object authorization and upstream credentials.
- Never commit secrets or expose them through model-visible tool arguments/results.
- No push, deployment or live destructive tests without explicit authorization.
- Write behavioral tests before runtime implementation and execute them.
- Distinguish mock, local protocol and live integration evidence.
- Keep release claims within the staging-only scope and verification recorded in README.md.
- Do not assume backend access, Poelis endorsement or licensing terms.
