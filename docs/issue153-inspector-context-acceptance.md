# Issue 153 — Inspector mutation feedback context

Baseline `2aabf623aa88f68c0050774ddb03f2773681abfe`; final integration onto release base `f84a6fb7d909f1bbeeb4d358ad66be9864b0affa` on 2026-10-22 (see the integration section below).

Inspector now fences mutation success, failure and finally by table + row context generation. Switching A → B → A invalidates the original request; changing revision/values for the same row does not. Switching context resets busy/message/error immediately, and an old request cannot clear a newer request's busy state. Unmount invalidates pending callbacks. Save/create, soft delete and historical restore share this fence. Requests still complete on the server; they are not aborted or cancelled.

Same-row values refresh continues updating the draft and comparison. Messages clear on context changes, preserving normal save feedback when revision/values refresh. Local errors still clear when the current row/columns refresh, preserving recovery after a 412 reload. Existing row-specific 412 conflict handling and If-Match boundary remain unchanged. This does not modify LibraryApp deletion/selection flow or permissions.

Validation (original head):
- `uv run pytest`: 429 passed, 19 existing warnings, 13.82 s.
- `uv run ruff check xeonolab tests scripts/verification/inspector-context/local_api.py`: passed. Verification helper formatted with Ruff.
- `uv run python scripts/export_openapi.py`: passed; no schema diff.
- Frontend `npm ci`, `npm test`: 62 passed, 0 skipped. `npm run build`: passed after final source change.
- `node scripts/verification/inspector-context/verify.mjs` from repository root: passed against actual temporary local FastAPI XeLibrary with actual TableView/Inspector in headless Google Chrome. A barrier in the synthetic client delays forwarding real requests; response bodies are never mocked. Synthetic development identity has only xelibrary:read/write. Temporary tables and rows are created through HTTP and deleted with their temporary data directory on exit.
- Chrome/API assertions: pending A save then B has no A success; reopen A shows saved values; same-row save feedback succeeds; A→B→A ignores old completion; concurrent B request stays busy after old A finally; true 412 from external legitimate writer is hidden on B, shown on A when still active, and server values remain unchanged; reloading clears both local error and conflict, and a retry successfully updates the server; real restore POST and DELETE success feedback stays out of B. Deletion is confirmed through real GET 404. The fixture retains table rows to isolate mutation feedback; LibraryApp deletion/selection flow is unchanged.
- Evidence: `runtime/verification/issue153-inspector-context/inspector-context-result.json`, `inspector-context.png` and command logs. `git diff --check` passes.

Re-run requires existing frontend npm dependencies, uv, Node supporting WebSocket, and /usr/bin/google-chrome (or CHROME_BIN). The tracked helper under scripts/verification/inspector-context builds only a loopback temporary service/browser context. No new application dependency, data migration or production dependency. Rollback by reverting this UI/helper/documentation change. Production rollout and production acceptance were not performed.

## 2026-10-22 final integration onto release base `f84a6fb`

The branch was merged with the current release base; the only conflict was `Inspector.tsx`, resolved by keeping both sides: the base imports (`Icon`, `normalizeRowValues`, `parseExactJSON`, `stringifyExactJSON`) and the PR's `useRef`, the base `normalizeRowValues` submit/restore values together with the PR's table+row context generation fence (success/error/finally guarded), the base `false`-return handling for `onCreate`/`onRestore`, and the PR's unmount invalidation. `git diff` against the base shows only `Inspector.tsx` plus this document and the verification helper.

Final integration gates on `f84a6fb`:
- `uv run pytest`: **520 passed**, 1 warning; `uv run ruff check xeonolab tests scripts`: passed; `export_openapi.py`: no API diff.
- Frontend `tsc --noEmit`: passed; `npm test`: **110 passed**; `npm run build`: passed.
- `CHROME_BIN=/opt/devagent/browser/chrome/chrome node scripts/verification/inspector-context/verify.mjs`: **9/9 checks PASS** against the real local FastAPI XeLibrary + actual TableView/Inspector in headless Chrome (success scoped to record, reopen saved value, normal same-row feedback, A-B-A invalidation, old finally cannot clear newer busy, old 412 hidden on B, current 412 shown with server data preserved, reload clears error/conflict and retry persists, restore/remove feedback isolated). Evidence regenerated under `runtime/verification/issue153-inspector-context/`.

The trusted protected preflight and current-head CI for the exact integration head remain the merge gate. Boundaries unchanged: requests are not aborted, only their feedback is fenced; no API/schema/migration/dependency change; the helper uses a synthetic local identity and temporary rows.
