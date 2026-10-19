# Scope and ownership

- Goal: Poelis hosts this MCP service for customers using compatible clients.
- Scope: complete documented public API coverage, including authorized deletion.
- Alberto owns product decisions and reviews; Poelis approves its identity integration
  and production deployment. No official endorsement is implied.
- Python, strict typing and Pydantic are required; see [decisions](decisions.md).

## Bootstrap boundary

- Configure the development environment; do not implement customer-facing tools yet.
- Consolidate `main` into one bootstrap commit; explicit force-push authorization applies
  only to this bootstrap, not future review branches.
- No automatic dispatch, reviews, merge or deployment.

## Remaining inputs

- Hosted identity/delegation contract; public credential docs alone do not provide it.
- Test credentials, disposable product and authorization for live destructive tests.
- Exact supported client versions, deployment constraints and license/ownership terms.

Missing evidence blocks the affected release gate, not independent local development.
