# Same-table existing attachment picker (#241)

The attachment panel now offers “选择已有附件” when the current identity has
read and attach scopes and a paged API helper is available. Users browse filename,
media type, byte size and a readable source-row label without leaving their target
record. Search submits a filename/display-label substring query, clear starts a
fresh query, and the list reports its loaded count separately from remaining pages.
Loading more is explicit; an empty scan page can still offer more. Read failures
retain loaded items and retry the failed page. A rejected bind keeps the picker
and displays its error; retry is explicit.

The existing manual ID, upload, authenticated download and unbind controls remain.
Only choosing a candidate or submitting the manual form invokes the existing bind
endpoint. The picker never downloads or previews contents. Its accessible Modal
uses the existing keyboard/focus primitive and gives each choice a filename and
source label. Row/table/authEpoch changes discard discovery state; generation
checks ignore obsolete query successes and failures. LibraryApp passes its current
identity epoch/subject through Inspector so even a stable API helper is fenced.
A completed old bind remains associated with the explicitly chosen original row
and cannot refresh or write the newly selected row.

## Additive API contract

`GET /api/v1/tables/{tid}/attachments?limit=25&q=...&cursor=...` returns the standard
`data[]` / `meta.next_cursor` envelope. Candidate data uses the existing attachment
metadata fields plus `source_label`, with an explicit generated OpenAPI model.
No object hash, content bytes, unrelated row values or global count is returned.

The endpoint requires `xelibrary:read`, current table read permission and current
source-row read permission. Every request goes through the existing identity/scope
boundary and loads current ACL policies. Deleted and missing source rows are skipped.
It examines at most 500 references plus one continuation probe, including hidden
rows, per request. `limit` defaults to25 and is bounded1–100; query length is at most
200 characters. Search uses Unicode casefolded substrings in filename or the
**displayed** label. The label is the first nonblank string in title, name, then
schema-ordered string/text columns, clipped to256 characters; Row ID is the fallback.
Other row fields are neither returned nor searched.

Results use the existing unique attachment-ID index in ascending keyset order.
A versioned opaque cursor binds the boundary to table and normalized query; its
position grants no authority and is checked as a canonical UUID. Invalid or
mismatched cursors return422. The next page independently applies current identity
and ACL, including changes after a previous page. Empty pages can have a cursor;
no client-side loop automatically reads them. This is live keyset pagination,
not a cross-request snapshot: newly inserted references behind the boundary or
newly readable earlier references require a fresh query (close/reopen or change
search). No new index, migration, object storage format or cursor-secret service
is required.

The existing bind operation still independently requires target `xelibrary:attach`
and row attach permission plus source read scope/ACL. It creates a new target
reference, retains the original, and reuses the content hash. Repeating a cross-row
bind creates another reference; binding a reference to its own row returns the
existing reference. This preserves the current API's behavior rather than imposing
new filename/hash deduplication.

## Executed local acceptance

Base refreshed from865a72277008a5773236b50b993bacdecc78d6ab to
3b8661e6545510ac4290ad5fd0c0d7bac836c545 before final required gates. The unrelated
three-file pool verification merge was fast-forwarded; all pending picker source
hashes were checked unchanged during that refresh.

- `uv run pytest`:492passed, one existing Starlette/httpx warning.
- `uv run pytest tests/test_attachment_picker.py -q`:13passed. Coverage includes
  metadata-only fields, filename/CJK/casefold/source-label search, label priority/
  clipping/fallback, invalid bounds/cursors, bounded empty pages, current ACL/scope
  changes between pages, deleted/orphan/cross-table exclusion, interleaved permissions,
  stable905-reference continuity under insertion/deletion, and existing bind scopes,
  target/source ACL, copy/reference/same-row/object-reuse semantics.
- `uv run python scripts/export_openapi.py`:only xelibrary OpenAPI changes, matching
  the added route/models; all existing endpoints remain.
- `uv run ruff check xeonolab tests scripts apps/desktop-web/tests/attachment-picker-local-api.py`:
  passed. `npm test`:73passed; `npm run build`:TypeScript/Vite passed.
- `CHROME_BIN=/usr/bin/google-chrome node --test apps/desktop-web/tests/browser-acceptance-attachment-picker.test.mjs`:
  8passed, zero page errors or external requests. Actual Attachments/Modal and
  LibraryApp/Inspector test explicit25+2 pages, search/clear/no results, failed-page
  retention/exact retry, denied bind/retry, old query success/rejection fencing,
  row/table/identity replacement, late old bind, native Enter/Space/Escape/focus,
  and380px long metadata without horizontal overflow.
- The same Chrome suite starts a **real candidate XeLibrary API** on loopback with
  temporary synthetic source/target rows and explicit development tokens. It checks
  actual paging/search, hidden-source exclusion, source/target/scope/outsider403,
  one explicit bind with source retained, and the manual-ID, upload, download and
  unbind paths. Its browser-memory fixture is separately used for controlled
  error/race cases. No production identity or data is used.

Evidence is under runtime/verification/issue241. Normal and narrow screenshots were
visually inspected. This is component/local API acceptance, not production, full
shell, screen-reader or remote CI acceptance. The scoped browser test is registered
in the existing regression runner; the entire broad browser runner was not repeated.

## Release dependency and rollback

The previously missing table metadata API is implemented locally, but remains an
unreleased production dependency until the matching XeLibrary API and desktop UI
ship together. An older API returns a visible picker read error; manual ID fallback
remains. Operator-managed production identity/authority, existing object storage
and TLS/routing provisioning remain unchanged. No new service/secret/schema migration
is introduced. Coordinator owns claim, committed head, PR/CI, merge and deployment
acceptance. Roll back the eventual additive implementation commit; existing references
and uploaded content require no reverse migration.
