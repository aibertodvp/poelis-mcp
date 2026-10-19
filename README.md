# Poelis MCP — v0, staging only

Official Poelis SDK tools over **Streamable HTTP, not stdio**, using the official MCP SDK.

## Getting started

**uv 0.13.0 is enough** on Linux/macOS; it manages Python 3.15 and dependencies.
**Nix + direnv are optional** and provide the pinned development environment.

In [staging](https://staging.poelis.com), open **Settings → SDK Keys → Create SDK Key**.
Copy your [personal SDK key](https://docs.poelis.com/python-sdk/sdk-keys); writes require Editor access.

### Server

From this repository, **without Nix**:

```sh
POELIS_SDK_KEY='YOUR_STAGING_SDK_KEY' uv run --locked python -m poelis_mcp start
```

**With Nix + direnv** (shell hook enabled): review `.envrc`, then run
`direnv allow .` and wait for the environment to load.

```sh
POELIS_SDK_KEY='YOUR_STAGING_SDK_KEY' just start
```

Inline keys can enter shell history. Omit the variable for a hidden prompt.
The server runs on loopback in the background for four hours. Poelis enforces your
SDK key's permissions; clients use a **separate local MCP bearer token**.
The SDK key stays server-side. This test issuer is not Poelis login.
No global executable or client-specific profile is installed.

`uv run --locked python -m poelis_mcp status` shows the URL and private token-file path.
`uv run --locked python -m poelis_mcp stop` stops the session without changing Poelis data.

### Agent (MCP client)

Replace the repository path below, then paste into any compatible coding agent on the same machine:

```text
Connect this agent to the already-running Poelis staging MCP server.
Run: uv run --locked --project /absolute/path/to/poelis-mcp python -m poelis_mcp status
Use this client's documented Streamable HTTP configuration and the reported URL.
Prefer user-level configuration so it works from other working directories.
Preserve unrelated servers and agent settings; do not create a special profile.
Load the private token file locally through a supported secret/header mechanism:
Authorization: Bearer <token>. Never print the token, put it in chat or tracked
files, or use the Poelis SDK key. If secure setup or user-level scope is unsupported,
explain the limitation; do not invent configuration options.
Reload if needed, discover tools, then list my workspaces and products. Read only.
```

After creating a new server session, repeat client setup: URL and token change.

## Scope and limits

17 tools through the Poelis SDK: **11 reads and 6 writes**.

| Area | Operations |
|---|---|
| Workspaces | List, get, user-accessible resources |
| Products | List, list versions, set baseline version |
| Items | List drafts, get draft, list version snapshot |
| Search | Products, items, properties |
| Properties | Update numeric, matrix, text, date, status |

No creation, deletion, files, raw GraphQL or REST fallback.
**Real writes affect existing accessible data.** Timeouts and cancellation do not
guarantee rollback; automatic retries are disabled. Version-list pagination is local
over bounded SDK responses.

SDKs: `mcp==2.3.0` and `poelis-sdk==1.3.1`.
Tool contracts: [operations.py](src/poelis_mcp/operations.py) · [models.py](src/poelis_mcp/models.py).

## Checks

Tests: `uv run --locked pytest`. Build: `uv build --no-sources`.
Optional **just** shortcuts: `just start` for the same server, `just check build` for full checks.

Local protocol tests cover all tools, mutations and auth isolation with fixture upstreams.
Live verification is read-only.
