# Account audit history pagination (#237)

`GET /api/v1/auth/accounts/audit/events` keeps the existing `data` array and
`meta.request_id`. Its optional `limit` defaults to 200 and accepts 1–1000;
`meta.next_cursor` is an opaque string when another authorized page exists and
`null` at the end. Pass it back as `cursor` without interpreting it. Invalid
limits/cursors return 422 (`INVALID_LIMIT` / `INVALID_CURSOR`).

Pages use `(at DESC, id DESC)` keyset ordering, including equal timestamps.
Each request resolves the caller's current identity and recomputes the account
subtree. An event remains visible when its actor **or** target is in that
subtree. The cursor only supplies an ordering boundary; it grants no access.
The sequence is not a frozen authorization snapshot: later pages reflect tree
and credential changes. New events newer than a loaded boundary appear after
an explicit recent-page refresh. Reading never changes audit events.

Accounts uses the existing `apiPage` envelope helper. “读取最近记录” replaces the
list with a recent page; “加载更早记录” explicitly appends the next page, deduplicated
by event ID. No history is prefetched. Loading disables concurrent reads;
errors preserve loaded rows and offer a retry of the same request. Terminal
pages show “历史已读完” and have no older-page control. Existing actor/target
name, login name and raw ID rendering is retained. API/subject/session-epoch
changes and unmounting invalidate pending completions. Settings remounts
Accounts on a session-epoch change to refresh its account tree too.

Validation:

- `uv run pytest`: synthetic SQLite events cover 207 records, timestamp ties,
  default/short/empty/terminal pages, invalid parameters, subtree changes,
  actor-or-target access and current credential revocation. The tree-change
  fixture alters storage locally; it does not introduce a reparent API.
- `uv run python scripts/export_openapi.py` and
  `uv run ruff check xeonolab tests scripts`.
- In `apps/desktop-web`: `npm test`, `npm run build`, and
  `CHROME_BIN=/usr/bin/google-chrome node --test tests/browser-acceptance-account-audit.test.mjs tests/browser-acceptance-account-patch.test.mjs`.
  Chrome loads the real React Accounts component with synthetic in-memory
  API pages. It checks explicit fetching, deduplication, scroll retention,
  retry, terminal state, and late completions after session/API changes or
  unmount. This does not demonstrate production data or deployment.

No new scope or provisioning dependency is required. Startup adds an
idempotent `(at DESC, id DESC)` index to the existing account audit table.

## Coordinator integration (2026-10-22)

The branch was refreshed onto release base `261cb0a` before integration. The only
semantic conflicts were `Accounts.tsx` (union with the newer account-create
validation form and `readableErrorMessage` helper) and the browser-regression
list. During resolution one duplicate legacy `建立子账号` block was accidentally
kept next to the newer validated form; the stale copy was removed so the panel
renders exactly one create-account form, and the full browser regression was
re-run afterwards.

Final gates on the merged tree: 576 pytest, full-repository Ruff, OpenAPI no diff
(only the new `cursor`/`limit` parameters), frontend unit tests, `tsc --noEmit` +
Vite build, the account-create and account-audit browser acceptances (8/8) and the
full 65/65 browser regression. Exact-head CI, protected preflight, approval and
deployment evidence are recorded on the PR.
