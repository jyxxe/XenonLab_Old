# Issue 187 — authenticated whole-table .xl snapshot download

Baseline `03fb8985bb18d7c3ae9a4a965c34f164907226a8`; branch `codex/general-issue187-xl-snapshot-download-20261003`. Local implementation only, not deployed.

The current table page shows “下载 .xl 快照” only to a loaded identity with xelibrary:admin. A small TableSnapshotDownload component calls the existing authenticated apiRaw GET `/tables/{id}/export`. It uses the server's current ASCII UUID.xl Content-Disposition filename, falling back to tableID.xl if absent. It does not send filter, selection or pagination parameters or load additional pages. The caption explicitly describes a full .xl data file including metadata, unaffected by current view filters/selection/pages, without attachment files.

A synchronous pending lock and disabled progress button prevent duplicate requests. Structured failures use existing APIRequestError/describeError/ErrorState, including code/request ID and retry. Successful status says only that browser download was initiated, not that disk persistence was confirmed. Blob URLs are revoked after the browser has received the link. This uses the existing browser Blob transport, not a streaming/background export job.

Parent keys the component by tableID, subject and authEpoch; component generation also changes with apiRaw. On table/view/account removal or API replacement, obsolete success/error/finally callbacks are ignored and old responses do not create a download in the new context. In-flight HTTP requests are not aborted. Table session/paging/filter/selection/Inspector operations are unchanged (LibraryApp has only import + render additions).

## Reproduce

```sh
cd apps/desktop-web
npm ci --offline --no-audit --no-fund
cd ../..
node scripts/verification/xl-snapshot-download/verify.mjs
```

Requires local Google Chrome, Node, uv and locked dependencies. Real LibraryApp/TableView/styles and actual local XeLibrary FastAPI are used, with uvicorn app-dir/PYTHONPATH/cwd fixed to this worktree. Temporary SQLite contains two self-owned synthetic tables; A has101 rows with title and int64 fields, B has one row. Explicit development admin token has read/write/attach/admin; same-subject scoped read-only token has read. The fixture `/users/me` describes those exact synthetic scopes because XeLibrary is not the desktop identity service. No production identity, data or external service is read.

Six Chrome groups passed with zero page errors:

1. Actual administrator button triggers native browser download, honoring the server UUID.xl filename; SQLite schema and all101 int64 values are read back. UI had100 loaded rows, local filter Row000 and one selected row. No cursor-page requests are introduced by download.
2. Proxy holds actual successful export bytes; button is busy, no success/download is reported, and repeated ordinary clicks produce only one GET.
3. Benign explicitly synthetic503 failure produces existing human-readable error with code/request ID and no download; retry succeeds through the actual endpoint.
4. Old success after switching from A to B is discarded and cannot mark B successful or start a download.
5. Old error after switching to B is discarded with no B alert/success.
6. The lawful read-only identity sees neither the action nor its section; it does not attempt the admin endpoint.

Success responses/files are real, not fabricated. Only failures are explicitly injected by the owned loopback proxy. In stale-response cases, the fixture's raw-response clone body completion counter and two animation frames establish that callbacks settled before new-table status assertions, rather than assuming proxy-send means client completion. No permission bypass tests or malicious files are used.

Evidence: runtime/verification/xl-snapshot-download/browser-report.json, actual UUID.xl file and browser.log; screenshots download-filtered-page.png,download-pending.png,download-error.png. Filtered download and error screenshots were visually inspected. Temporary API data, fixture copies and servers are removed in finally.

## Gates

- `uv run pytest`: initial478 passed/1 failed in unchanged `tests/test_pool_telemetry.py:317` (expected408, received empty socket bytes); targeted rerun1 passed, full rerun479 passed/19warnings. Initial and rerun logs retained.
- `npm test`:62 passed.
- `npm run build`:passed.
- `uv run ruff check xeonolab tests`:passed.
- `uv run python scripts/export_openapi.py`:passed,no schema diff.
- `node --check scripts/verification/xl-snapshot-download/verify.mjs`:passed.
- Chrome helper:6 groups passed,0 page errors.
- `git diff --check`:passed.

TableSnapshotDownload SHA256: `92b64199110bff0048b88853ef7c65333bccc02abae8e3490917693d595c1787`.

No dependencies, migrations, backend APIs/scopes/formats or persistent jobs added. Rollback is reverting the two LibraryApp additions and removing the component. This does not implement#84 asynchronous import/export or include object-store attachment bytes. Production authentication, real large-file behavior and deployment remain unverified; local acceptance is not a production release. No remote writes, commits, pushes or deployment performed by this agent.


Latest coordinator acceptance on base `e74301b03fab2cde4e50e395c02bc041862cde85`: 479 pytest/19 warnings on first parent run,62 frontend tests,scoped Ruff xeonolab/tests,unchanged OpenAPI,build,six actual local API/Chrome groups,0 page errors;filtered download and structured-error screenshots inspected. Synthetic local data only; no production acceptance.
The coordinator initially ran broader `ruff check .`;11 existing errors outside xeonolab/tests were reported in untouched infrastructure scripts. That exploratory log is retained. The repository-scope Ruff gate passed; no unrelated scripts were changed.


Latest coordinator acceptance on base `1aa3d1f7d698c9340b1b38568fd175a974afd012`: 479pytest19warnings firstpass62frontend/Ruff/OpenAPIunchanged/build/6actualAPIChrome0errors/SQLite101rows. The product source is unchanged from the frozen implementation. Synthetic local services/data only; no deployment or production acceptance.


Coordinator acceptance on current integration base `2c3481d96f8ba602be5418a44882ba0aa25187db`: 479pytest19warnings firstpass64frontend/build/Ruff/OpenAPIunchanged/6actualAPIChrome0errors/SQLite101rows. The base includes the already merged #190 move picker and #193 filter tree; this Issue’s functional additions remain scoped. Synthetic local data/services only; no production action by this Harness.


Coordinator refresh requested by maintainer comment1646: replayed without conflicts onto integrated `eecec3ec4e171b7536681bf872b5898faf7ba25f`. Product snapshot component hash remains `92b64199110bff0048b88853ef7c65333bccc02abae8e3490917693d595c1787`; the helper’s baseline metadata was updated. Final independent gates: **479 pytest passed, 19 warnings (16.21s)**; **66 frontend unit tests passed**, build, scoped Ruff, unchanged OpenAPI, syntax and whitespace passed. Actual local API/Chrome passed all **six groups, zero page errors**; the native browser download was read as a SQLite snapshot with exactly 101 rows despite a 100-row loaded page and local filter. Final filtered-download and explicit synthetic-error screenshots were inspected. Logs/evidence are `runtime/verification/xl-snapshot-download/parent-eecec3-*`. Historical telemetry failure and successful CI retry run307 remain recorded in the PR; no product change was made to hide that failure. Temporary services/identities/data only; no production action.


Before publishing the refresh, #220 was externally integrated as `03fb8985bb18d7c3ae9a4a965c34f164907226a8`. The coordinator replayed this branch without conflicts onto that current tip, preserving its upload-store wiring. Final acceptance on 03fb: **479 pytest passed, 19 warnings (14.02s)**; **68 frontend unit tests passed**, build/scoped Ruff/unchanged OpenAPI/syntax/whitespace passed; all **six real local API/Chrome groups passed, zero page errors**, exact 101-row SQLite export rechecked, two final screenshots inspected. Earlier eecec3 evidence is preserved in `parent-eecec3-evidence/`; final logs are `parent-03fb-*`. Snapshot component source hash remains unchanged. No production action or new dependency.
