# Issue 229: retain restored and newly created targets across a first-page refresh

Verified implementation baseline: `865a72277008a5773236b50b993bacdecc78d6ab`. Initial unmodified reproduction was on `743c5f3e9a184d74466a7d26c1aa1347234ad447`; those historical artifacts remain separate.

History restore and Create now use the exact row returned by the existing restore/create APIs. The row is updated immediately, then retained if the refreshed first page does not contain it. If the server page already contains that row, its fresher value wins. The server next_cursor is preserved; Load More uses its existing deduplication and reaches the real end without duplicating the retained row. This is a display-window repair, not a full-table load.

A monotonic row mutation context generation tracks API/APIPage clients, authEpoch, subject, table, active row, selection and view. A change, including A→B→A or unmount, invalidates the old mutation's subsequent refresh, row application and Library notice. Invalidated restore/create branches return an explicit private `false` result; Inspector skips the respective success message on that result. Existing `void` callbacks still represent ordinary success, and current errors still reject to show a real conflict. The refresh hook checks both its existing view generation and this optional guard before applying data or errors. Ordinary callers still use the existing default refresh behavior. No backend, API, scope, migration or dependency changes were introduced; exact int64 JSON handling and merged #194 Recent activation behavior are preserved.

## Re-run

With locked dependencies and system Chrome installed, from the repository root:

```sh
node scripts/verification/history-restore-page2/acceptance.mjs
SOURCE_CASE=before865 node scripts/verification/history-restore-page2/acceptance.mjs
```

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

- `uv run pytest`:479 passed,1 warning.
- `uv run ruff check xeonolab tests scripts/verification/history-restore-page2/local_xelibrary_api.py scripts/verification/history-restore-page2/local_desktop_api.py`:passed.
- `uv run python scripts/export_openapi.py`:passed; no generated API diff.
- `npm test --prefix apps/desktop-web`:74 passed.
- `npm run build --prefix apps/desktop-web`, node syntax and git diff --check:passed.

The context guards here cover rows, cursor, active-row continuity, Library notices and the explicit cancelled-restore/create success result. This narrow Inspector callback change does not implement #153/PR157's generic save/remove feedback or finally-state lifecycle. Normal current restore keeps existing API/feedback behavior; current412 remains a genuine error. The identity test refreshes authEpoch for the same actual development actor; it is not a production login or two-account switching test. Default list/detail deleted-row filtering and #218's independent Recent deleted-row path remain unchanged. #198's activation navigation fence is not implemented here. Ordinary explicit Refresh remains a first-page operation, rather than permanently pinning records into every future view.

No deployment is performed. Rollback is reverting this frontend change. No external workers or production services are required for this local display fix; real production authentication/activation remain outside this validation.

Create before control: `SOURCE_CASE=create-before` uses the archived Restore-only local implementation in `runtime/verification/history-restore-page2/pre-create-source` when present. Its Restore13 and small-table Create control pass, then the genuine off-page POST201/detail200 case fails selected target0 !=1; `create-before/create-pagination.json` and screenshot retain that evidence. This is a staged Restore-only implementation control, not an unmodified865a baseline. Restore-only acceptance remains archived under restore-only-current; the final combined acceptance is current/browser-report.json and create-final-browser-errors.log. Final combined gates are create-final-{pytest,unit,build,ruff,openapi}.log.

Independent parent final review reran all30 combined Restore/Create browser checks with zero page errors and matching four source hashes under `parent-final/`. Parent gates passed479pytest/1warning,74frontend tests/build, scopedRuff, OpenAPI unchanged, helper syntax and diff checks. Logs: `parent-final-{browser,pytest,unit,build,ruff,openapi}.log`. Screenshots confirm exact off-page created ID and selected restored r3 History. These are separate from the earlier Restore-only parent review.
