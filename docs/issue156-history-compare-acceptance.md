# Issue #156: two-endpoint history comparison

Baseline `2aabf623aa88f68c0050774ddb03f2773681abfe`; final integration onto release base `efab85a3d18dbf680a6dba569f5504c343426fac` on 2026-10-22. Branch `codex/general-issue156-history-compare-20261003`.

Inspector history now offers two accessible native selects: the viewed revision and a comparison revision. The comparison defaults to the current row and can use any already loaded historical snapshot. Headings and each value identify current/historical revisions; existing changedFields semantics compare both snapshots and same values receive an explicit equality message. Values remain JSON-rendered, with stacked labelled columns on narrow screens. Switching table/record clears comparison state. Comparison stays local and sends no mutation or extra snapshot request.

The existing restore function, write-scope check, busy state and current-row revision If-Match remain unchanged. Added state/reset and history UI are independent hunks from pending PR #157's mutation context and #166's controlled tab wiring; integration must retain both. There is no hard functional dependency, no copied mutation logic, and no LibraryApp change.

## Verification

- Original head gates: `uv run pytest` 429 passed, frontend 62 passed, Ruff/OpenAPI/build and the 8-check Chrome helper passed.
- `uv run ruff check xeonolab tests scripts/verification/history-compare`: passed.
- `uv run python scripts/export_openapi.py`: passed; generated schema unchanged.
- Frontend `npm test`: 62 passed, zero skipped.
- Frontend `npm run build`: TypeScript/Vite passed after final accessibility labels.
- `node scripts/verification/history-compare/verify.mjs`: PASS, eight browser/API checks, zero page errors.
- `git diff --check`: passed.

The reproducible runner starts a temporary local FastAPI XeLibrary and constructs synthetic r1/r2/current r3 through real create/update APIs. Real Chrome renders LibraryApp/Inspector and verifies default r1/current r3 values, keyboard-selectable r2 baseline, both endpoints and equality, no write requests or revision changes from comparison, 390px stacked values, record-switch baseline reset, restore and read-only behavior. Restore sends `If-Match: "revision:3"`, creates r4 with the exact r1 values, and API reads confirm all four snapshots remain. Read-only comparison works; every restore button is disabled and an actual read-only API restore attempt returns 403. Synthetic development identities and fixture identity response are local test sources. No production API, login or worker was contacted.

Browser report, command logs and visually reviewed desktop/mobile screenshots are under `runtime/verification/issue156-history-compare/`. Desktop shows JSON differences for the historical endpoints; mobile shows both labelled values in a single column. This evidence does not establish production usage frequency or production acceptance.

Fresh deduplication uses Forgejo `issues?type=issues&state=all&limit=50` through the last page: 109 distinct Issues/3 pages, with no PR markers; PRs counted separately: 59/2 pages. No competing #156 PR found.

## Operations

No backend API/schema, permission changes, dependency, migration or production worker. Production deployment has not been performed. Production acceptance remains with the coordinator. Rollback: revert the comparison UI/state/CSS and verification/document files, restoring comparison against current values only. Existing history and restore semantics are unaffected.

## 2026-10-22 final integration onto release base `efab85a`

The branch was merged with the current release base. Two conflicts were resolved by keeping both sides:

1. `Inspector.tsx` compare values: this change's labelled 查看/对照 blocks and `comparison` snapshot are kept, but each side renders with the base's `stringifyExactJSON` helper (exact int64-safe JSON) instead of `JSON.stringify`. The merged code retains the base's `normalizeRowValues`/restore contract and the already-merged #157 table+row mutation-context fence and controlled-tab wiring; this change only adds independent history-comparison state and UI.
2. `library.css`: the base's column-order/copy rules and this change's `.history-compare label`/`.history-diff` rules are both kept.

The Chrome helper was adapted to current base interaction: the desktop opens the Inspector on a single row click, and a scripted double-click can race the layout shift caused by the Inspector pane opening (the second click may land on a different row). The helper now uses the real single-click activation; the product behavior and assertions are unchanged.

Final integration gates on `efab85a`:
- `uv run pytest`: **521 passed**, 1 warning; `ruff check xeonolab tests scripts`: passed; `export_openapi.py`: no API diff.
- Frontend `tsc --noEmit`: passed; `npm test`: **112 passed**; `npm run build`: passed.
- `CHROME_BIN=/opt/devagent/browser/chrome/chrome node scripts/verification/history-compare/verify.mjs`: **8/8 checks PASS, zero page errors** against real LibraryApp/Inspector and a temporary local XeLibrary API (default current comparison and values, keyboard historical baseline, both endpoints and same-version equality, comparison sends no writes and current revision unchanged, 390px stacked values, record switch resets baseline, real restore uses current If-Match and retains history, read-only comparison allowed with restore disabled/API 403). Evidence regenerated under `runtime/verification/issue156-history-compare/`.

The trusted protected preflight and current-head CI for the exact integration head remain the merge gate. Boundaries unchanged: comparison stays local (no mutation or extra snapshot request), restore/write-scope/If-Match semantics untouched, no API/schema/migration/dependency change.
