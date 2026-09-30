# ADR 0005 — XeLibrary `fast_text` ANN on Qdrant

Status: Accepted for implementation; production live acceptance is pending.

## Context

Plan.md §§10, 13 and 14 specify D100M as the default 256-dimensional text
embedding and `fast_text` as the permanent cosine vector space. ADR 0004 keeps
`.xl` as the source of truth and identifies Qdrant as the production path for
avoiding a Python exact scan. The previous local candidate reached Qdrant but
did not configure the production service and left remote points behind after
row deletion and purge.

## Decision

- XeLibrary uses the Qdrant REST API over HTTPS with an API key loaded from a
  private file. Production requires the configured endpoint, key file and CA;
  it does not fall back to the SQLite bootstrap index when Qdrant is missing or
  unavailable. Development without Qdrant may use the explicit SQLite exact
  bootstrap.
- The named `fast_text` vector is 256-dimensional cosine. Every point carries
  table ID, row ID/revision, chunk ID, column, path, model revision and chunker
  version. Deterministic point IDs make reindex retries idempotent.
- Qdrant results are candidate hints. Each result is re-authorized against the
  current catalogue ACL and loaded from `.xl`; row revision, path, model and
  chunker metadata plus the current local index reference must match before
  any result or snippet is returned. Snippets come from current `.xl` values.
- Row update, soft delete, purge, restore, table move and table deletion remove
  affected remote points before committing the corresponding `.xl` mutation.
  Qdrant errors fail the mutation closed with a service error; the source
  transaction rolls back. Reindex upserts within the locked current-revision
  transaction and cleans known obsolete point IDs. A failed or interrupted
  rebuild cannot make a stale revision visible because query validation gates
  on `.xl` and the committed local index reference.
- The Qdrant API key remains provisioned on VM104 by the coordinator. Generated
  production service configuration references its file path and trusted CA;
  neither key content nor a private key is added to a release artifact.

## Consequences and limits

Qdrant is now a required production dependency for semantic search and the
indexing lifecycle. Cleanup calls are synchronous while the `.xl` write lock
is held, so the operation fails closed but a slow/unavailable Qdrant can delay
row and table mutations up to the bounded HTTP timeout. Qdrant is rebuildable;
`.xl` rows, revisions and local index references remain authoritative.

The live unique-collection acceptance script is
`scripts/accept_qdrant_fast_text.py`. It must be run by the coordinator from
the approved VM104 environment and removes only its own temporary collection.
Passing local mock tests is not production acceptance; the live script result,
production deployment configuration and real semantic API flow remain separate
acceptance evidence.
