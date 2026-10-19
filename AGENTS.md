# Repository constraints

Read README.md and docs/BRIEF.md before changing scope. Target: a self-hostable,
Poelis-operated MCP service. Current state: typed Python and Nix bootstrap;
no application server implemented yet.

## Development environment

- Run checks/builds through `nix develop --no-update-lock-file -c just check build`.
- For direnv, review `.envrc`, then run `nix develop -c direnv allow .` and
  `nix develop -c direnv exec . just check build`; an installed shell hook also works.
- Use Nix-provided Python and uv, not host tools.
- Preserve `flake.lock` and `uv.lock`; update dependencies deliberately, never during tests.
- `nix flake check` validates the shell, not Python tests. Execute the just recipes.
- Python annotations are mandatory; keep mypy strict, Pydantic validation and tests passing.

## Delivery and safety

- Alberto reviews and approves changes.
- One writer per target; preserve unrelated work.
- Inspect official API/SDK contracts; never guess endpoints or compatibility.
- Separate caller authentication, object authorization and upstream credentials.
- Never commit secrets or expose them through model-visible tool arguments/results.
- No push, deployment or live destructive tests without explicit authorization.
- Write behavioral tests before runtime implementation and execute them.
- Distinguish mock, local protocol and live integration evidence.
- Do not call the server production-ready until docs/ACCEPTANCE.md is satisfied.
- Do not assume backend access, Poelis endorsement or licensing terms.
