# ADR 0006 — Additive CJK text search for XeLibrary

Status: Accepted for implementation; production rollout is pending.

## Context

The current `__xl_fts` table uses SQLite FTS5 `unicode61`. Chinese text without
spaces is tokenized as one token, so a keyword that is only a substring of a
stored sentence is not found. Rewriting the existing FTS body in place would
change English token boundaries, damage BM25 behavior, and make a code rollback
depend on a production reindex.

## Decision

- Keep `__xl_fts` content and query semantics unchanged. Add an independent
  `__xl_fts_cjk` FTS5 table with CJK characters separated into tokens. English
  words stay contiguous in this secondary index.
- Update both indexes in the same `.xl` transaction on row create, patch,
  soft-delete, purge and restore. A schema update rebuilds the existing legacy
  index with its existing raw body format and rebuilds the CJK index from active
  `.xl` rows. Creating or mutating an older table adds the secondary index
  transactionally; search reads never change files.
- Treat every FTS hit as a candidate. Current `.xl` values, current row/table
  ACLs, path scope, active-row status and exact field filters remain the
  authority before returning a result.
- For every CJK query, scan an ordered, bounded prefix of eligible active rows
  for literal substring matches, even when either FTS index already returned
  hits. Merge results by `(table_id,row_id)`, keeping the indexed score for a
  duplicate. `XELIBRARY_CJK_SCAN_ROWS` defaults to 5,000 and is clamped to
  1–100,000. A scan that leaves eligible rows unchecked sets `meta.partial` and
  `meta.approximate` true. Substring results have `score_type=substring_scan`
  and `approximate=true`. A one-row lookahead distinguishes an exact cap
  boundary from remaining rows without an unbounded `COUNT(*)`; rows rejected
  by row-level ACL still consume budget.
- No full reindex is required for rollout. Existing `.xl` files get the
  additive table lazily when mutated, and the changed row is indexed; a schema
  update rebuilds all active rows. The bounded scan covers other legacy rows
  in the meantime.

## Rollback compatibility

The old binary continues reading and updating the unchanged `__xl_fts` table
and ignores the extra FTS table. It does not need a reverse migration, and
`.xl` row data is never rewritten solely for CJK support. If old code modifies
rows while the new index remains on disk, it can become stale. The new code
therefore revalidates secondary-index candidates against current row values;
missing candidates are recovered by the bounded scan and incomplete coverage
is surfaced through `partial`. A future rollout can rebuild the secondary
index by the existing schema-update transaction; no unbounded production scan
is introduced.

## Consequences

- CJK scans add bounded local SQLite row reads and ACL checks per query. The
  default 5,000-row cap bounds this work; operators may tune it up to 100,000.
- A large legacy table may produce a partial result until an existing write or
  schema rebuild creates the secondary index and subsequent searches fit
  within the scan budget. This is explicit in response metadata.
- English queries continue using the unchanged legacy FTS/BM25 path.
- There are no new runtime dependencies and no OpenAPI shape changes.
