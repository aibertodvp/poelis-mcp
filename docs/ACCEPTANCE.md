# Production acceptance

These are release gates, not bootstrap claims. Application implementation is pending.

- [ ] Complete API-to-tool coverage, supported client versions and real HTTP tests.
- [ ] Verified hosted authentication, audience/scopes and tenant/object isolation.
- [ ] Authorized live read/write/delete/file tests, readback and cleanup.
- [ ] Pagination, upstream errors, partial writes, bounded retries and secret redaction.
- [ ] Non-root container; health/shutdown, resource limits and secure proxy configuration.
- [ ] Deployment, rollback and credential rotation exercised by the operator.
- [ ] Dependency/security review, distribution terms and Poelis deployment approval.

Record actual checks and limitations in the relevant PR. Mocked or in-memory tests
never prove HTTP authentication, upstream permissions or production readiness.
