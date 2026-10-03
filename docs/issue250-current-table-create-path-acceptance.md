# Issue 250: current table path for new tables

Refs #250. Baseline: `865a72277008a5773236b50b993bacdecc78d6ab`.

When a table is actually visible in the library view, opening New Table now initializes the editable path from that table. Search, query, theorem and welcome views use `/知识`, even when the session retains a table. Every closed-to-open transition refreshes only the path. Existing name/schema drafts and validation remain unchanged; edits made while open remain editable. The existing authenticated POST and server validation are unchanged.

## Reproduce

With the repository Python environment, desktop npm dependencies and `/usr/bin/google-chrome` available, run from repository root:

```sh
SOURCE_CASE=before node scripts/verification/current-table-create-path/acceptance.mjs
node scripts/verification/current-table-create-path/acceptance.mjs
```

The helper starts isolated loopback XeLibrary and Desktop APIs with temporary SQLite storage and an explicitly synthetic development administrator. Tables are created through the real API. Chrome renders actual LibraryApp, NewTableDialog, SchemaEditor and styles. This does not exercise production/OIDC identities or workers. Temporary fixture copies and API data are removed on exit.

Before mode substitutes exact 865a source for both product files: a visible `/数学/分析` table still initializes `/知识`. The before browser report records effective source hashes. Current mode passes nine checks: nested path; cancellation without POST and path-only reset preserving name/schema; switching to a deep table; edited path POST201 and schema GET readback; root path; search/query/theorem defaults; welcome default and original name/path/schema validation. Page errors: zero.

Evidence: `runtime/verification/current-table-create-path/{before,current}/browser-report.json`. Final before log: `before-final.log`; final current log: `current-layout.log`. Visually inspected `current/deep-path-default.png` and `current/created-edited-path.png`: complete path, preserved draft fields and created table path are visible without clipping.

## Validation and unsuccessful attempts

Final `uv run pytest`: 479 passed, one existing Starlette/httpx deprecation warning, 12.36s. `npm test`: 73 passed, zero failed/skipped. `npm run build`: passed. Scoped Ruff (`xeonolab`, `tests`, helper Python files), OpenAPI export, node syntax and diff whitespace checks passed. OpenAPI has no change. Logs use the `final-` prefix in the evidence directory.

Preserved early attempts are not counted as passes. `before.log` failed before startup because npm dependencies were unavailable; identical-lock cached dependencies were then linked. `before-ready.log` and `before-path-selector.log` failed on fixture accessible-name selectors, corrected to the existing title and help-text label. `current.log` exposed a genuine first implementation timing bug: passive initialization left the previous edited path visible immediately after reopening. The implementation now uses a layout effect for the opening transition; `current-layout.log` passes without an added wait masking that assertion.

No backend, scope, dependency or migration changes. Production activation is not performed. Rollback is the two frontend hunks. Other pending LibraryApp changes are not incorporated by this task.

## Independent coordinator acceptance

The coordinator reran all nine current actual Chrome/local Desktop+XeLibrary API checks, inspected both path/created-table screenshots and matched both product source hashes. The independent exact865a before control confirms the existing nested table initializes /知识; both effective baseline source hashes match Git. parent-current/ and before/ browser reports plus parent-current.log/parent-before.log preserve these executions; child-before/ preserves the prior child control.

First independent full pytest failed the unchanged pool regression at its original line317 (expected408, empty reply): 478 passed,1 failed,1 existing warning in12.78s. Both that test and production Receiver were verified byte-identical to865a. parent-pytest.log is preserved and the observation was published on #248, whose dedicated fix is PR252; it was not mixed into this frontend patch. The failed case then passed alone (0.52s), and the final full rerun passed479 cases (one existing warning,14.41s). parent-pytest-target.log and parent-pytest-final.log retain the distinct reruns. This does not identify the external failed deployment’s unique cause.

Independent frontend73 tests/build, Ruff for xeonolab/tests/helper, OpenAPI export (no generated diff), node syntax and whitespace checks passed. No production/OIDC or worker validation, merge or deployment is claimed.

## Integration refresh

After the independent checks, the external maintainer merged PR252 as3b8661e6545510ac4290ad5fd0c0d7bac836c545. This feature branch was rebased onto that actual integration commit without conflicts. Its outgoing diff remains the two frontend hunks, five local helper files and this document; the pool test fix is upstream. The only intervening integration changes are the pool test/helper/document, so all frontend and API implementation bytes used for the above browser/frontend checks remain identical. Both product hashes still match the accepted report. Post-rebase full pytest passed479 cases with one existing warning in13.97s; Ruff and OpenAPI export/no diff/whitespace checks passed again. parent-rebased-*.log preserve those results. No production deployment result is asserted from the merge.
