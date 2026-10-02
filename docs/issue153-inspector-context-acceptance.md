# Issue 153 — Inspector mutation feedback context

Baseline `2aabf623aa88f68c0050774ddb03f2773681abfe`.

Inspector now fences mutation success, failure and finally by table + row context generation. Switching A → B → A invalidates the original request; changing revision/values for the same row does not. Switching context resets busy/message/error immediately, and an old request cannot clear a newer request's busy state. Unmount invalidates pending callbacks. Save/create, soft delete and historical restore share this fence. Requests still complete on the server; they are not aborted or cancelled.

Same-row values refresh continues updating the draft and comparison. Messages clear on context changes, preserving normal save feedback when revision/values refresh. Local errors still clear when the current row/columns refresh, preserving recovery after a 412 reload. Existing row-specific 412 conflict handling and If-Match boundary remain unchanged. This does not modify LibraryApp deletion/selection flow or permissions.

Validation:
- `uv run pytest`: 429 passed, 19 existing warnings, 13.82 s.
- `uv run ruff check xeonolab tests scripts/verification/inspector-context/local_api.py`: passed. Verification helper formatted with Ruff.
- `uv run python scripts/export_openapi.py`: passed; no schema diff.
- Frontend `npm ci`, `npm test`: 62 passed, 0 skipped. `npm run build`: passed after final source change.
- `node scripts/verification/inspector-context/verify.mjs` from repository root: passed against actual temporary local FastAPI XeLibrary with actual TableView/Inspector in headless Google Chrome. A barrier in the synthetic client delays forwarding real requests; response bodies are never mocked. Synthetic development identity has only xelibrary:read/write. Temporary tables and rows are created through HTTP and deleted with their temporary data directory on exit.
- Chrome/API assertions: pending A save then B has no A success; reopen A shows saved values; same-row save feedback succeeds; A→B→A ignores old completion; concurrent B request stays busy after old A finally; true 412 from external legitimate writer is hidden on B, shown on A when still active, and server values remain unchanged; reloading clears both local error and conflict, and a retry successfully updates the server; real restore POST and DELETE success feedback stays out of B. Deletion is confirmed through real GET 404. The fixture retains table rows to isolate mutation feedback; LibraryApp deletion/selection flow is unchanged.
- Evidence: `runtime/verification/issue153-inspector-context/inspector-context-result.json`, `inspector-context.png` and command logs. `git diff --check` passes.

Re-run requires existing frontend npm dependencies, uv, Node supporting WebSocket, and /usr/bin/google-chrome (or CHROME_BIN). The tracked helper under scripts/verification/inspector-context builds only a loopback temporary service/browser context. No new application dependency, data migration or production dependency. Rollback by reverting this UI/helper/documentation change. Production rollout and production acceptance were not performed.
