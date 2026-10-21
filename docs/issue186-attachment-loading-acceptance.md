# Issue 186 — distinguish attachment loading, empty and error

Baseline `e74301b03fab2cde4e50e395c02bc041862cde85`, branch `codex/general-issue186-attachment-loading-20261003`. Local implementation only.

Attachments has independent listStatus/loading/loaded/error and listError. Initial and refresh reads show “正在读取附件列表…”. Only a current successful response containing [] shows “当前记录没有附件。”. A failed read shows a labelled alert and “重试读取附件”; retry calls the existing GET. Loaded items remain visible during refresh and after refresh failure. List error is independent of mutation status, so a successful upload cannot conceal a failed metadata refresh.

The existing active table/row key and request-generation guards protect success/error list status updates. Inspector's table+row remount contract is unchanged. No upload, association, download, unbind, scope or backend request code was changed; no dependency/migration/API schema added. Rollback is reverting this small Attachments state/render patch.

## Reproduce

```sh
cd apps/desktop-web
npm ci --offline --no-audit --no-fund
cd ../..
node scripts/verification/attachment-loading/verify.mjs
```

Requires local Google Chrome, Node, uv and locked dependencies. Vite loads actual LibraryApp, useTableSession, TableView, Inspector and Attachments. Backend uvicorn app-dir/PYTHONPATH/cwd all point to this source. Temporary SQLite, a table, three rows and normal small text attachments are created through the actual local XeLibrary API. Setup dev identity has xelibrary:read/write/attach; a same-subject narrower synthetic dev token has read only. Fixture `/users/me` describes those explicit test scopes because XeLibrary has no desktop identity endpoint; actual library data and authorization responses come from the real local service. No production identity or files are read.

The loopback proxy forwards requests to real FastAPI and buffers actual successful list responses. Promise barriers assert upstream response ready and not sent, then release explicitly; no arbitrary delay is used to infer pending. Only named failure cases replace delivery with benign synthetic503. In A→B→A cases, an exact completed-call count confirms obsolete response delivery and two animation frames allow callbacks/render to settle before asserting the current request is still pending. Original request guards remain intact.

Eight actual Chrome groups passed with zero page errors:

1. Nonempty row pending is neutral; release shows exact real metadata.
2. Empty row pending is neutral; release confirms empty.
3. Initial failure is an error, not empty; GET retry recovers.
4. Successful ordinary upload followed by failed list refresh preserves old items; retry shows new metadata.
5. A→B→A old success cannot finish or populate the new pending view.
6. A→B→A old error cannot show an error in the new pending view.
7. Ordinary upload/unbind/bind remain correct. Bind uses B's existing live attachment reference; deleting a last association removes that reference, so an unbound-only ID is not reused.
8. Same-owner read-only token lists and downloads exact text bytes, without upload/unbind controls; its component requests are GET only.

Evidence: runtime/verification/attachment-loading/browser-report.json, browser.log, nonempty-pending.png, failed-refresh-retains.png and readonly-loaded.png. Pending and retained-error screenshots were visually inspected. Temporary fixture copies, API data and servers are cleaned in finally; held plans are released on failure too.

## Gates

- `uv run pytest`:479 passed,19warnings (includes concurrent temporary pytest directory cleanup warnings).
- `npm test`:62 passed.
- `npm run build`:passed.
- `uv run ruff check xeonolab tests`:passed.
- `uv run python scripts/export_openapi.py`:passed,no schema diff.
- `node --check scripts/verification/attachment-loading/verify.mjs`:passed.
- Chrome helper:8 groups,0 page errors.
- `git diff --check`:passed.

Attachments SHA256: `e44bfc490f491963ff1d33d20ce82614e399d4e88370ef0718364925fa7221bf`.

No production dependency is added. Production latency, accounts and deployment are not verified; this is actual local API/Chrome acceptance with explicit synthetic identities/data. PDF/PNG preview architecture (#152) and Inspector history/mutation/tab pending PRs are outside this patch. No commit/push/remote writes or deployment performed by this agent.

Coordinator independently reran all gates after rebase to merged #179 (`e74301b`):479 pytest/19warnings on first run,62 frontend tests,build,Ruff,unchanged OpenAPI,diff check and all eight actual local API/Chrome groups passed,0pageerrors. Loading/failed-refresh-retained screenshots visually inspected.
