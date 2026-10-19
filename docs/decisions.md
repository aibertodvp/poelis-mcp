# Bootstrap decisions

Research snapshot: 2026-10-19. Documentation/source inspection only; no customer API calls.
This is implementation input, not an approved behavioral spec.

## Stack

| Choice | Reason |
|---|---|
| Python 3.15+, Pydantic v2 | Required; typed interfaces and runtime boundary validation |
| Official `mcp==2.3.0` | Streamable HTTP and OAuth resource-server support without a second web framework |
| Direct REST adapter | Full public-operation coverage; the Python SDK exposes a narrower GraphQL surface |
| uv lock, mypy strict/plugin, Ruff, pytest | Small reproducible development toolchain; no orchestration framework |

The installed MCP SDK lists 2026-07-28 and older handshake revisions, including
2025-11-25. This is SDK capability, not tested Claude/Codex compatibility.
Use its supported negotiation, not a handwritten compatibility layer.

## Public API coverage

Computed from [OpenAPI](https://docs.poelis.com/openapi.json): **25 operations**, 15
literal path entries: 12 GET, 6 POST, 4 PATCH, 3 DELETE. API metadata version 1.0.0.

| Resource | Operations | Coverage |
|---|---:|---|
| Workspaces | 2 | List/get |
| Products | 5 | List/get/create/update/delete |
| Items | 6 | List/get/create/update/delete/bulk create |
| Properties | 6 | List/get/create/update/delete/bulk create |
| Files | 5 | List/get/update metadata/upload/download |
| Organization | 1 | List users and access rights |

- No public operations found for publishing, comments, tasks, file deletion or workspace mutation.
- Product deletion is permanent and cascading; item/property deletion has draft/history rules.
- File listing by product can return workspace files: a query parameter is not an authorization boundary.
- Uploads are multipart; downloads are binary. Do not invent server-local paths as a remote-client upload interface.
- Snapshot/hash the schema in the implementation change and maintain operation-to-test coverage there;
  do not generate every tool blindly from OpenAPI or duplicate the full schema in prose.

## What authentication documentation establishes

| Surface | Verified contract | Limitation |
|---|---|---|
| REST | Admin-created bearer key; all resources in one organization | No native per-user restriction conveyed by that key |
| Python SDK | User-bound SDK key; visibility follows user; writes require Editor | Not full REST coverage; key interchangeability undocumented |
| Organization users | Auth0-subject IDs, workspace roles, product overrides (`no_access` wins) | Directory/ACL data, not authentication of the caller |

- Inspected `poelis-sdk==1.3.1` uses GraphQL and lacks REST-equivalent creation/deletion/file coverage.
- Its constructor does not validate the key remotely; local baseline/change logging is enabled by default.
- SDK README key onboarding differs from current credential docs; prefer explicit REST/SDK documentation.
- No delegated OAuth flow, token exchange, `/me`, key-owner introspection or authoritative tenant-binding
  contract was found in the inspected public docs. Absence from docs is not proof it does not exist internally.
- A successful read can test key acceptance, but cannot prove which human owns it or its intended tenant.

## Hosted authentication direction

- MCP is an OAuth resource server using Poelis's approved identity provider; do not implement a new login server.
- Validate issuer/audience/expiry/scopes; bind verified subject to tenant and allowed objects server-side.
- Keep Poelis REST credentials separate, provisioned by authorized admins and selected from verified membership.
- Never forward MCP tokens to Poelis, accept credentials in tool arguments, or match users by email alone.
- Full REST coverage with native per-user permission parity needs Poelis's delegation contract or an agreed
  authorization policy. Do not silently grant organization-wide key privileges to every logged-in customer.
- An explicitly admin-authorized organization-wide integration is an alternative product scope, not an assumed fallback.

**Remaining operator input:** issuer/audience/client-registration details, trusted user/tenant binding,
permission/delegation model, and a disposable test account. These block hosted auth acceptance,
not typed REST-client development. No customer-facing auth is implemented in this bootstrap.

## Typing and validation

- mypy strict + Pydantic plugin reject untyped functions and mistyped model construction; explicit `Any` is forbidden.
- Tool input models should reject coercion and unknown fields. Parse wire-format UUID/date strings deliberately;
  decide upstream additive-field compatibility separately rather than applying `extra=forbid` blindly everywhere.
- `SecretStr` masks common representations, not every leak. Never return/log raw validation error input payloads.
- Bootstrap tests exercise the toolchain only. HTTP auth tests must use real HTTP; SDK in-memory clients bypass it.

## Sources

- [REST credentials](https://docs.poelis.com/authentication.md), [SDK keys](https://docs.poelis.com/python-sdk/sdk-keys.md)
- [Organization ACLs](https://docs.poelis.com/api-reference/organization/list-organization-users.md)
- [Poelis SDK source, v1.3.1](https://github.com/PoelisTechnologies/poelis-python-sdk/tree/v1.3.1)
- [MCP SDK auth, v2.3.0](https://github.com/modelcontextprotocol/python-sdk/blob/v2.3.0/docs/run/authorization.md)
- [MCP authorization](https://modelcontextprotocol.io/specification/latest/basic/authorization)
- [Pydantic mypy plugin](https://github.com/pydantic/pydantic/blob/v2.14.1/docs/integrations/mypy.md)
