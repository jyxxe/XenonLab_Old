# Issue #215 — Query Console current-page TSV acceptance

Acceptance baseline: `865a72277008a5773236b50b993bacdecc78d6ab`, including merged #216 / PR #224.
Work claim epoch 1 was verified by the coordinator; this document records local
acceptance, not a deployment or real OIDC/data validation.

## Result

Query Console offers **复制当前结果页为 TSV（N 行）** when it has returned rows.
The clipboard contains five columns in response order: `表`, `路径`, `Row ID`,
`Revision`, `值`. Row IDs and values JSON are copied in full, rather than from
the shortened display. Unknown catalogue names use the full source table ID.
Metadata nulls become empty cells; nulls, arrays and objects inside values remain
JSON. Values use the same `stringifyExactJSON` as Library parsing/editing; unsafe int64 values remain exact JSON integer tokens. The action consumes the currently loaded page and makes no API requests.

The three generic TSV files are byte-for-byte copies of the independently
reviewed PR #148 branch at `d983e08b9d4b12e4b87d7c93d1d6e113bf55a5ac`:
`library/tsv.js`, `library/tsv.d.ts`, and `tests/tsv.test.mjs`.
This reuses its quoted tab/CR/LF/doubled-quote contract and CRLF record separators.
Table View selection behavior from that unmerged PR is not part of this change.

## Actual local checks

| Check | Actual result |
| --- | --- |
| `uv run pytest` | 479 passed, 1 existing warning |
| `uv run ruff check xeonolab tests scripts apps/desktop-web/tests/query_copy_api.py` | Passed |
| `uv run python scripts/export_openapi.py` | Passed; generated OpenAPI has no diff |
| `npm test` in `apps/desktop-web` | 79 passed |
| `npm run build` | TypeScript and Vite passed |
| `node --test --test-concurrency=1 tests/browser-acceptance-query-results-copy.test.mjs tests/browser-acceptance-query-drafts.test.mjs tests/browser-acceptance-int64.test.mjs` | 6 passed, 0 skipped, 0 browser page errors |
| `git diff --check` | Passed |

The final backend run reports the existing FastAPI/httpx TestClient deprecation. No new runtime dependency was added.

## Synthetic real API and native clipboard

`tests/query_copy_api.py` starts the actual XeLibrary FastAPI application on an
ephemeral localhost socket, creates two temporary owned SQLite tables and four
synthetic rows through its API, then cleans up the data directory on exit.
Its development token is a fixed synthetic test value. Only `/users/me` is a
clearly labelled synthetic identity adapter; table and Query requests execute
the real implementation and ACL checks.

The browser runs the real LibraryApp/QueryConsole with:

```text
FROM "/S215/**" WHERE status = "ready" ORDER BY amount DESC LIMIT 10
```

It returns three rows across both tables, ordered 30/20/10, excluding a fourth
draft row. Clicking the product button writes to Chromium's native clipboard;
`navigator.clipboard.readText()` reads it back. Independent Python `csv.reader`
recovers the header plus exactly three five-cell records and parses every JSON
cell back to the returned values. A source table name contains actual tab,
CRLF and quotes; nested JSON contains null, tab, CRLF, quotes, Unicode, objects,
arrays, zero, false and a 220-character string. All roundtrip correctly.

The real API also returns int64 values `9223372036854775807`,
`-9223372036854775808`, and `9007199254740993`, including both endpoints
inside JSON objects and arrays. The browser adapter uses `parseAPIJSON`,
and assertions confirm actual UI values are BigInt. Python generates
independent decimal JSON expectations and parses the native clipboard TSV
with arbitrary precision integers, checking raw unquoted number tokens and
unchanged numeric strings. Neither expected values nor copied integer values
are compared through two ordinary JavaScript parses.

Both the frontend call log and server API request log remain identical across
copy. Expression, current rows and pagination are unchanged. The existing
**复制查询** action copies only the DSL. A second run with LIMIT 2 followed by
**下一页** uses the existing `LIMIT 2 OFFSET 2` query contract; copying then
produces only its final loaded row, with no additional requests or offset edits.

## Failure, retry and asynchronous boundaries

Standalone controlled QueryConsole fixtures exercise native clipboard success,
then explicit rejected writes and a missing Clipboard API. The failure alert
retains the three-row page; restoring the API and clicking the same button
successfully retries without any data request.

Controlled delayed promises verify:

- Two synchronous clicks submit one write.
- A pending old page does not block the new page's copy.
- Old success does not appear on the new page, and its finally block cannot
  release the new operation's busy state.
- Running, validating, query error and unverified identity disable copying;
  late rejection is discarded after each state transition.
- Editing clears the result action; unmounting discards late feedback.
- Existing same-page identity revalidation and stale Query response protection
  still pass their browser regressions.

Desktop and 390px screenshots were inspected. The new toolbar action wraps and
fits the mobile width; the standalone failure alert is legible. Existing mobile
LibraryApp pane scrolling and horizontally scrollable result tables are unchanged.
Evidence is in `runtime/verification/issue-215/`: `native-clipboard.tsv`,
`native-api-evidence.json`, `desktop-copy.png`, `mobile-copy.png`,
`mobile-copy-failure.png`, and the test logs.

## Integration and limits

The feature is integrated locally on the actual merged PR #224 baseline.
The QueryConsole preview and TSV mapper both use the existing exact JSON
serializer; the API fixture uses its matching parser. Final checks on this
Issue branch passed 479 backend tests, 79 frontend tests and all six browser
regressions, including Library integer editing, Query identity isolation and
native Clipboard integer fidelity. Logs end in `final865.log` under
`runtime/verification/issue-215/`. This is a tested local integration; it does
not mean the feature branch has been pushed, merged or deployed.

The generic TSV object branch and future PR #148 Table View copying still
need their own nested-BigInt composition check. Query TSV sends explicitly
serialized JSON text to that encoder. Decimal and exponent literals retain
Library's ordinary Number semantics; exact integer tokens are preserved.

Browser clipboard writes already submitted cannot be cancelled on a later page
or identity change; the implementation discards stale UI feedback. The real-API
browser companion requires the Python environment prepared by `uv run pytest`.
A local frontend-only run without that environment reports this companion as skipped.
The merged frontend CI prepares Python fixtures with `uv sync --locked`; final local
acceptance had no skipped browser tests. Standalone Clipboard regressions run through
`npm run test:browser-regression`.

No production/OIDC acceptance, external spreadsheet paste, PR publication,
deployment or database migration was performed. Rollback is a revert of this
feature's eventual commit. Full platform production dependencies remain as
recorded in ADR 0001 and `docs/implementation-status.md`.

## Review1802 follow-up: spreadsheet text policy

The Query page marks free-text provenance columns (`表`, `路径`, `Row ID`) as spreadsheet text. When their string value starts with `=`, `+`, `-`, or `@` after leading whitespace/control/format characters, or has a leading control/format character, the TSV encoder adds one leading apostrophe before the original text. Tabs/newlines/quotes are still escaped as a single cell. The marker is the only transformation; all original characters follow it unchanged. Ordinary provenance, null metadata, numeric revision and booleans retain their previous bytes. Consumers reading TSV as plain data must remove that one protective marker from affected provenance cells to recover the original name/path/ID.

The `值` column remains the unmodified `stringifyExactJSON` output, including raw signed int64 tokens, nested arrays/objects/null and quoted strings. This is a complete valid JSON literal rather than free formula text; the policy does not prefix JSON number tokens or numeric JSON strings. Spreadsheet importers can still infer numeric types and lose precision when users parse a JSON number cell as a number; the clipboard itself retains the exact JSON text. The check does not claim cross-product spreadsheet execution or import behavior. No spreadsheet formula was executed.

Shared `serializeTSV` exposes the optional `spreadsheetTextColumns` list. Its default is empty, preserving existing generic scalar/header/object behavior. Query opts in only columns0–2. There is no Table View caller in this branch; independent PR148 is not silently changed and must choose/verify its own column policy on composition.

Actual follow-up validation on head015fa61a plus this patch: `uv run pytest`479passed/1existing warning; scoped Ruff passed; OpenAPI regenerated with no diff; `npm test`81passed; `npm run build`passed; targeted Query copy/drafts/int64 Chrome7passed/0skipped/0pageerrors. Two new unit cases cover opt-in/default behavior, inert operator/control prefixes, scalar compatibility and untouched JSON int64. Real temporary API native Clipboard acceptance now has inert operator/control-led table names, keeps original text after the marker and asserts no extra API calls. A separate native Clipboard controlled UI case checks9inert metadata prefixes and independent Python CSV/JSON integer parsing. These cases use only local synthetic data and do not open an external spreadsheet. Logs: `runtime/verification/issue-215/text-policy-*.log`; Clipboard artifact: `spreadsheet-text-native-clipboard.tsv`.

## Coordinator integration (2026-10-23)

The branch was refreshed onto release base `0f404989`. `tsv.js`/`tsv.d.ts` were an
add/add conflict because the base already ships the earlier exact-integer TSV
serializer; the branch's opt-in `spreadsheetTextColumns` superset was kept and both
unit suites were retained. `LibraryApp.tsx` gained only the `tableNameForCopy` prop
that the console copy path needs.

The browser acceptance requires the repository `.venv` interpreter for its
independent Python CSV/JSON parser; the worktree needed a `.venv` symlink (test
environment issue, not a source change). Final gates: 579 pytest, full-repository
Ruff, OpenAPI no diff, frontend unit tests, tsc + Vite build, the query-results copy
acceptance (4/4) and the full 71/71 browser regression. Exact-head CI, protected
preflight, approval and deployment evidence are recorded on the PR.
