# Issue 229: retain restored and newly created targets across a first-page refresh

Verified implementation baseline: `865a72277008a5773236b50b993bacdecc78d6ab`; final integration onto release base `a824d42e1115c14219958df4cf825f93d00556d5` on 2026-10-22 (see the integration section below). Initial unmodified reproduction was on `743c5f3e9a184d74466a7d26c1aa1347234ad447`; those historical artifacts remain separate.

History restore and Create now use the exact row returned by the existing restore/create APIs. The row is updated immediately, then retained if the refreshed first page does not contain it. If the server page already contains that row, its fresher value wins. The server next_cursor is preserved; Load More uses its existing deduplication and reaches the real end without duplicating the retained row. This is a display-window repair, not a full-table load.

A monotonic row mutation context generation tracks API/APIPage clients, authEpoch, subject, table, active row, selection and view. A change, including A→B→A or unmount, invalidates the old mutation's subsequent refresh, row application and Library notice. Invalidated restore/create branches return an explicit private `false` result; Inspector skips the respective success message on that result. Existing `void` callbacks still represent ordinary success, and current errors still reject to show a real conflict. The refresh hook checks both its existing view generation and this optional guard before applying data or errors. Ordinary callers still use the existing default refresh behavior. No backend, API, scope, migration or dependency changes were introduced; exact int64 JSON handling and merged #194 Recent activation behavior are preserved.

## Re-run

With locked dependencies and a headless Chrome installed, from the repository root:

```sh
CHROME_BIN=/opt/devagent/browser/chrome/chrome node scripts/verification/history-restore-page2/acceptance.mjs
SOURCE_CASE=before865 CHROME_BIN=/opt/devagent/browser/chrome/chrome node scripts/verification/history-restore-page2/acceptance.mjs
```

The helper honors `CHROME_BIN` and falls back to `/usr/bin/google-chrome`.

The current command passes 30 checks with zero page errors and writes `runtime/verification/history-restore-page2/current/browser-report.json`. The before865 command loads exact unmodified LibraryApp/hooks from865a722; after genuine restore and first-page response consumption plus two animation frames, it intentionally fails the retained-row regression (actual0, expected1). Its effective source hashes and trace are in before865/unfixed-trace.json. The earlier743c control observed a successful API r3 but no selected target/new-record Inspector, while its first-page control stayed selected; see before/browser-report.json and before-virtual-retry.log. This earlier control is not claimed as a complete865a replay.

## Actual fixture and checks

Actual LibraryApp/TableView/Inspector and stylesheet render in headless Chrome. Local XeLibrary and Desktop API factories run from this worktree in fresh loopback temporary directories. A configured synthetic development human creates the restore table and101 lawful synthetic rows via normal API requests; two rows are normally soft-deleted using current If-Match. `/users/me` now comes from the actual local Desktop API, rather than the original audit fixture's static response. This does not verify production OIDC, credentials or rollout.

1. First-page History restoration keeps the selected row at revision3.
2. Second-page restoration keeps the exact selected row, correct label and History at revision3, with no new-record Inspector.
3. The refreshed first-page cursor remains available; subsequent Load More deduplicates the retained target and reaches the real end at101 loaded rows.
4–7. Genuine delayed restore POST responses after new selection, A→B→A, table navigation or authEpoch refresh do not start an old-context refresh, alter the newer selected row, or display a cancelled restore success message.
8–11. Genuine delayed first-page refresh responses after those same transitions preserve the newer selection/table and cursor, without a cancelled restore success message. In ABA cases the exhausted cursor stays exhausted, proving that a numerically identical final row ID does not revive the old operation.
12. A real optimistic concurrency conflict returns412, preserves the current selected record and reports the conflict without a false refresh.
13. A delayed genuine412 after selecting another row does not refresh or replace the newer selected context, report an old conflict, or display restore success.

14–16. A small-table Create control and a 300-row Create table use normal UI POST201 and direct detail200 readback. The created UUID is confirmed absent from the first100 server rows, remains selected with its correct values, and retains the server cursor. Further Load More reaches the actual full count without duplicating the retained row.
17–28. Genuine delayed Create POST201 and first-page GET200 after selection, new-form→row→new-form ABA, table change, actual same-actor authEpoch refresh, replacement API clients, or unmount/remount cannot activate the old created target or show either Inspector/Library creation success. Invalidated POSTs do not start their old refresh.
29–30. Normal deletion of an owned empty fixture table causes a genuine subsequent UI Create404. The current error remains visible; buffering that error until another table is selected suppresses the old error and false success.

Create uses the exact POST row during guarded refresh and applies its own selection only after the final await/guard. Thus its legitimate null→created-row transition is not treated as external cancellation. Temporary Create tables,300 setup rows and ordinary empty-table deletion controls are separate legal local API data, not production/worker/OIDC evidence.

Barriers buffer real API status/headers/body, assert readiness before transitions, then wait for client body consumption and two animation frames before conclusions. No response payload is fabricated. Initial normal pagination and mouse selection are retained; combination setup uses legitimate Recent navigation and actual grid Arrow keys to avoid virtual-row auto-scroll/click instability. All setup navigation finishes before the next operation. API/current-row values and history are checked independently of transient success messages.

Five visual checks: current/create-small-control.png; current/create-off-page-target.png; current/first-page-restored.png; current/page2-before-restore.png; current/page2-after-restore.png. The last screenshot shows selected r3, the record Inspector and History, plus the retained cursor's Load More entry.

A separate review counterexample is retained in `pre-cancel-source`, with source-hashes.json: the initially proposed #229 code resolved a stale operation without distinguishing cancellation, allowing Inspector to display restore success. `SOURCE_CASE=cancel-before` uses that archived local snapshot (when present), intentionally fails the new no-success assertion (actual1, expected0), and saves cancel-before/cancel-feedback-failure.json/png. The additional `SOURCE_CASE=cancel-before412` control reaches the genuine delayed412 and then fails the same no-success assertion (actual1, expected0), with cancel-before412/cancel-feedback-failure.json/png and cancel-before412.log. These are staged-implementation controls, not unmodified git baselines. The final30 current checks cover late successful POST, refresh, ABA/authEpoch and late412 cancellation; current/browser-report.json includes Inspector among four actual source hashes. Pre-review13 evidence remains in pre-cancel-review-current and is not the final cancelled-feedback acceptance.

## Gates and boundaries

- Original head gates: `uv run pytest` 479 passed,1 warning; frontend 74 passed; build, Ruff, OpenAPI export and syntax/diff checks passed.
- Final integration gates on base `a824d42` (2026-10-22): `uv run pytest` **517 passed**, 1 warning (100.22s); `npm test --prefix apps/desktop-web` **108 passed**; `npx tsc --noEmit`, frontend build, `uv run ruff check xeonolab tests scripts`, `uv run python scripts/export_openapi.py` (no API diff), helper `node --check` and `git diff --check`: passed.

The context guards here cover rows, cursor, active-row continuity, Library notices and the explicit cancelled-restore/create success result. This narrow Inspector callback change does not implement #153/PR157's generic save/remove feedback or finally-state lifecycle. Normal current restore keeps existing API/feedback behavior; current412 remains a genuine error. The identity test refreshes authEpoch for the same actual development actor; it is not a production login or two-account switching test. Default list/detail deleted-row filtering and #218's independent Recent deleted-row path remain unchanged. #198's activation navigation fence is not implemented here. Ordinary explicit Refresh remains a first-page operation, rather than permanently pinning records into every future view.

No deployment is performed. Rollback is reverting this frontend change. No external workers or production services are required for this local display fix; real production authentication/activation remain outside this validation.

Create before control: `SOURCE_CASE=create-before` uses the archived Restore-only local implementation in `runtime/verification/history-restore-page2/pre-create-source` when present. Its Restore13 and small-table Create control pass, then the genuine off-page POST201/detail200 case fails selected target0 !=1; `create-before/create-pagination.json` and screenshot retain that evidence. This is a staged Restore-only implementation control, not an unmodified865a baseline. Restore-only acceptance remains archived under restore-only-current; the final combined acceptance is current/browser-report.json and create-final-browser-errors.log. Final combined gates are create-final-{pytest,unit,build,ruff,openapi}.log.

Independent parent final review reran all30 combined Restore/Create browser checks with zero page errors and matching four source hashes under `parent-final/`. Parent gates passed479pytest/1warning,74frontend tests/build, scopedRuff, OpenAPI unchanged, helper syntax and diff checks. Logs: `parent-final-{browser,pytest,unit,build,ruff,openapi}.log`. Screenshots confirm exact off-page created ID and selected restored r3 History. These are separate from the earlier Restore-only parent review.

## 2026-10-22 final integration onto release base `a824d42`

The branch was merged with `feature/platform-v1` at `a824d42e1115c14219958df4cf825f93d00556d5` (the release that contains PR #210). Conflicts were resolved without dropping either side: `hooks.ts` keeps the PR's `retainedRow`/`isCurrent` refresh arguments together with the base `sort` argument and view-generation checks; `logic.js`/`logic.d.ts` keep both `retainRefreshedRow` and the base `orderedColumns`/`moveColumn`/`clampColumnWidth`/`keyboardColumnWidth`; the logic test file keeps both test groups and the merged import list. `git diff` against the base shows only the PR's four source files, its unit test, this document and its verification helper.

The current base changed two things the original helper assumed, so only the helper harness (not the product assertions) was adapted and this is recorded honestly:

1. **Session isolation #219**: `LibraryApp` now remounts `LibraryWorkspace` when `authEpoch` changes. For the two `identity` transitions the stale response must still be rejected (no cancelled restore success, no stale refresh request, no old conflict), but the remounted fresh workspace no longer keeps the previous row selection or exhausted cursor by design. Those transitions now assert the stale-rejection guarantees only; `selection`/`ABA`/`table` transitions keep the full selected-row and exhausted-cursor assertions. This reflects deliberate base behavior, not a weakened product guarantee.
2. **Virtualized rows / settled refresh**: the helper now activates the last loaded row by scrolling it into the virtual viewport and clicking its real row (instead of a long ArrowDown loop that detaches the row between window updates), and re-scrolls a selected row into view before reading `aria-selected` after a settled refresh. `CHROME_BIN` is honored so the helper runs on this VM as well as CI/dev hosts.

Final run: `current/browser-report.json` — **30/30 checks passed, zero page errors**, all five screenshots regenerated. Effective source hashes in that report:

- `src/library/LibraryApp.tsx`: `62fd042708971957a4c52e64de03012aa04450140f51428de9864a5842409707`
- `src/library/Inspector.tsx`: `762124d6483b36fc489f07ad16891bf78b35f8a9680a1cc74b6eb33163633828`
- `src/library/hooks.ts`: `06ba7ed47c4e8c9792758601d94762da550a0830d4e6bdf82255de1cedb100d3`
- `src/library/logic.js`: `24816d1620a78e29ce9fed352ae0addf1c8b86ac01a584065597c9210cf0416a`

The trusted protected preflight and current-head CI for the exact integration head remain the merge gate; the local Chrome run above is coordinator evidence, not a production/OIDC test. Long-standing boundaries from the original acceptance are unchanged: this is a display-window repair, no backend/API/schema/dependency change, no deployment in the PR itself.
