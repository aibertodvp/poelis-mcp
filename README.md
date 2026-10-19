# Poelis MCP

Self-hostable MCP service intended for Poelis to operate for its customers.

**Bootstrap only:** typed Python package and development checks.
No application tools, customer login, server entrypoint, container or deployment yet.

| Choice | Bootstrap |
|---|---|
| Environment | Nix + flake-parts; committed `flake.lock` |
| Python | 3.15 from Nix; typed package |
| MCP | Official Python SDK 2.3.0; Streamable HTTP target |
| Validation | Pydantic 2.14.1; strict boundary validation |
| Checks | mypy strict + Pydantic plugin, Ruff from Nix, pytest |
| Dependencies | uv 0.13.0 from Nix; committed `uv.lock` |
| Delivery | Alberto reviews and merges changes |

## Development

Prerequisite: Nix with `nix-command` and `flakes` enabled. No host Python, uv,
Node.js or just installation needed. Linux x86_64/aarch64 and macOS Apple Silicon.
The pinned nixpkgs branch no longer supports Intel macOS.

```sh
nix develop --no-update-lock-file -c just check build
```

Or use direnv (review `.envrc` before allowing it):

```sh
nix develop -c direnv allow .
nix develop -c direnv exec . just check build
# With direnv installed and its shell hook enabled: direnv allow, then just check build.
```

- Nix fixes the tools and Python interpreter; uv installs hash-locked dependencies,
  including the build backend, before building without isolation.
- Ruff comes from the locked nixpkgs (0.16.10), not a PyPI executable requiring a
  conventional Linux loader. No global `nix-ld` configuration is needed.
- `just` rejects execution outside the Nix environment. No global nix-direnv setup needed.
- `just check`: lint, formatting, typing and tests.
- Typing covers `src` and `tests`: strict mypy, all `disallow_any_*` checks,
  additional optional diagnostics and the Pydantic plugin. `just typecheck` disables
  incremental checking so unused configuration sections can be detected.
- Regression tests exercise the pinned checker against unsafe snippets and a valid
  typed control. Inferred concrete types are allowed; redundant annotations are not required.
- Casts, targeted suppressions and `no_type_check` are not universally prohibited by
  mypy: they require explicit review, not silent use to make checks pass.
- `just build`: wheel and source distribution using the locked build backend.
- CI runs the same Nix commands. `nix flake check` validates the shell; it does **not**
  run pytest. Use the `just check` command above for the actual tests.
- First setup requires network access to Nix caches and Python package artifacts;
  this is a locked development environment, not a fully sandboxed Nix Python build.
- Update `flake.lock` and `uv.lock` deliberately; keep uv's required version aligned.
- Tests currently verify the toolchain, not Poelis integration or authentication.
- No secrets needed for bootstrap checks. Never commit credentials.

## Project notes

- [Scope and ownership](docs/BRIEF.md)
- [Technical findings and decisions](docs/decisions.md)
- [Production acceptance](docs/ACCEPTANCE.md)
- [Working rules](AGENTS.md)

Coverage target: documented public APIs, including authorized deletion, not every
web-app feature. Client compatibility and live API tests remain unverified.
Ownership/license must be agreed before distribution; no open-source license assumed.
