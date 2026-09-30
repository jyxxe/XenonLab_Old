# Issue #11 production acceptance — 2026-10-01

Release commit `c9e095108b678e98037fc123bb15dabae54ed7e7` completed through
Deploy Gateway. Release `e6cb8491-dab2-4ec4-bed7-56795d195235` created
deployment `bec2b250-f25d-4312-b8f4-d23cd5b8233f`; the deployment completed
and its lease was released.

Before activation, XeLibrary writes were paused for a fresh protected local
snapshot. The 6-file snapshot passed manifest/hash/catalogue/SQLite validation,
and an isolated restore rehearsal completed. The source snapshot and verified
backup are under `runtime/backups/2026-10-01-issue11/` on the release operator
host; this is same-host operational backup evidence, not offsite backup.

Coordinator validation for the release: `uv run pytest -q` passed all 234
tests; scoped Ruff passed; OpenAPI export produced no `docs/api` diff; and
`git diff --check` passed. The seven service readiness probes passed across
VMs 101, 104, 106, 107 and 111. The public desktop, Forgejo login page and OIDC
discovery each returned HTTP 200.

From the deployed VM104 release as the `xeonolab` service account,
`scripts/accept_qdrant_fast_text.py` created a uniquely named temporary
collection, upserted and queried one point, received one result, and deleted
its collection in cleanup. VM104's Qdrant URL, API-key file and CA settings
were installed as protected runtime files; the key contents were not logged or
included in the release artifact.

This proves the production Qdrant dependency, credentials, TLS path and isolated
create/upsert/query/delete lifecycle. It does not yet prove a positive semantic
search through XeLibrary using an authenticated production API client or a
production reindex of existing table data. Issue #11 therefore remains open
until that end-to-end API path is safely verified.
