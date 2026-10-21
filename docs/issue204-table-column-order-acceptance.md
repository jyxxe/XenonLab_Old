# Issue #204 — Per-table field display order

Baseline: `3af21d3b6e8e4a23aa18ea05ddcad8140645f875` (the branch was merged with this release base during final integration on 2026-10-21). Branch: `codex/general-issue204-table-column-order-20261003`.

## Behavior

Table View exposes “调整字段列顺序”. The native disclosure and each 前移/后移 button are keyboard accessible. Adjustments immediately reorder schema field headers and their matching values. Row ID and 更新时间 remain before fields, 状态 remains after fields, and the selection checkbox remains fixed at the beginning. “恢复默认字段顺序” uses the current schema order.

The preference is local to this browser and table, matching existing local column-width preferences. The key is `xeonolab.library.column-order.{table.id}`, containing field names only. It is not account-scoped or synchronized across devices. Hydration only reads; explicit user edits write the current table key. State carries its owner table ID so a previous table's order cannot render or persist into the next table during hydration. Invalid JSON/shape uses schema defaults. Storage failures keep the current in-memory view but cannot guarantee survival across reloads.

`orderedColumns` preserves original schema objects, ignores unknown/removed/duplicate entries and appends new schema fields. `gridColumns` derives from this one displayed field list and continues to drive both headers and cells. Widths and sorting remain keyed by name, not position. No schema, API, data, scope, backend, dependencies or migration change.

## PR #148 integration

PR #148 (`copySelected`, TSV copy) is merged into `feature/platform-v1`. Its `copySelected` builds both TSV headers and row cells from `gridColumns`, then appends the status column. This branch retains exactly that single ordering contract: `gridColumns` derives from this Issue's `displayColumns`, so reordering fields reorders both the rendered headers/cells and the TSV output. The Chrome helper now selects a row after reordering and asserts the copied TSV header order, header/cell binding, fixed system/status columns and that copying performs no extra API request. Other independent row-activation/navigation changes (#191/PR194, #195/PR198) are not included.

## Verification

Run from the worktree root:

```sh
npm ci --offline --no-audit --no-fund --prefix apps/desktop-web
node scripts/verification/table-column-order/verify.mjs
uv run pytest
uv run ruff check xeonolab tests
uv run python scripts/export_openapi.py
npm test --prefix apps/desktop-web
npm run build --prefix apps/desktop-web
node --check scripts/verification/table-column-order/verify.mjs
git diff --check
```

Backend: **517 passed** (single httpx/starlette deprecation warning). Frontend: **107 passed**, including two meaningful pure order-resolution/movement tests; build passed. Scoped Ruff passed; OpenAPI export produced no tracked API diff. Node syntax and whitespace checks passed.

Chrome ran actual LibraryApp/TableView and actual styles with the repository's synthetic fixture. All identity/API responses are stubs. Its 100 seed rows yield 99 initially visible loaded records (the existing deleted record is excluded); explicit next page yields 100. The helper adds a second field to the second table and exposes the first table's synthetic schema to simulate schema refresh through normal table-opening routes. No real backend, OIDC, worker or production data is exercised. localStorage and all browser interactions are real. Temporary fixture files are removed on normal runner completion; no tracked fixture changes.

**11 checks passed, 0 page errors:**

1. actual keyboard four moves place files first; header/value binding, fixed system/status columns, Inspector and no API requests
2. reordered display order drives TSV headers and cells through the single `gridColumns` contract; fixed system/status columns and no API fetch
3. same-table localStorage order survives actual browser reload
4. A/B/A/B switching preserves independent orders; no cross-table hydrate/persist write
5. schema removal ignores stale preference and new field appends with correct cell binding
6. keyboard default restore uses current schema, removes preference key and preserves other table preference
7. real pointer resize follows field name through reordering and persists in existing per-table width key
8. sorting, Arrow/Shift/Enter/Ctrl+A/Escape selection, Inspector, horizontal scrolling and bounded virtual rows remain functional
9. explicit row pagination retains display preference and renders appended row
10. 900px existing floating Inspector is explicitly closed; visible order controls remain keyboard reachable with immediate correct reordering
11. invalid stored preference falls back to schema without hydration rewriting storage or affecting another table

At 1440×900, files' header bounds were asserted entirely within the actual grid viewport while the Inspector remained open. Four screenshots were visually inspected: `files-first-with-inspector.png`, `tsv-reordered-order.png`, `ordered-columns-scroll-pagination.png`, `keyboard-order-narrow.png` under `runtime/verification/table-column-order/`. The 900px layout uses the existing floating Inspector; acceptance closes it through its real button before testing visible controls, not a hidden programmatic focus behind that panel. Reordering does not promise to eliminate total horizontal overflow.

Initial helper attempts had incorrect existing Inspector/button selectors, an obstructed narrow click, and assumed newly paginated rows stayed in the same virtual viewport. Only helper selectors/steps were corrected; final pagination waits for the actual loaded count and two animation frames, explicitly scrolls to the end and observes the appended row. The final runner uses actual Tab traversal to files' button, repeated native Enter presses and asserts retained focus during intermediate moves. No arbitrary sleep or fake completion assertion.

Source SHA-256 (final integration head):

- `src/library/TableView.tsx`: `f524d995da86a9c1bed84c056714c0c716a50431f55146b35cee20eb68d5f00b`
- `src/library/logic.js`: `c757a6e0e17e5cab2a78f85fecc7ccd89f904a2d937f6166756d1722bf09cea1`
- `src/library/library.css`: `6e9fddd791f7ea40693a23387311ed0c4d8104d00ca61e4742c993afad57753b`

## Delivery / rollback

Local implementation is frozen for coordinator review; no commit, push, merge or deployment was performed by this agent. No new production dependency or migration. Rollback is the frontend change revert; the new local preference key can remain harmlessly unused. Existing PostgreSQL/Qdrant/model/OIDC production dependencies are outside this UI-only Issue and were not validated here.

## Coordinator independent acceptance

The coordinator merged current `feature/platform-v1` `3af21d3b6e8e4a23aa18ea05ddcad8140645f875` into the branch and resolved the overlaps with the now-merged PR #148/#205-era changes without dropping TSV copy behavior. The `logic.js` import list was merged; the base column-width helpers (`clampColumnWidth`, `keyboardColumnWidth`) were kept and the conflicting legacy `resizeColumn` declaration was removed; logic/CSS/test blocks from both sides were retained and the merge-boundary syntax repaired. The Chrome helper was updated for the current base: expected widths now start from measured header widths, sort-indicator assertions use the SVG icons, Ctrl+A selection uses the actual loaded-row count, and a TSV-copy-under-reordered-columns check was added (header order, header/cell binding, fixed system/status columns, no API fetch).

Final integration head: the latest commit on this branch, which contains this document and the helper update. Local coordinator gates on that head: **517 pytest passed**, **107 frontend tests**, build, Ruff, OpenAPI with no API diff, helper syntax and `git diff --check` PASS. Chrome helper: **11 checks / 0 page errors**; four screenshots independently reviewed. The trusted protected preflight and current-head CI must pass on the exact head before merge; CI `verify / backend` and `verify / frontend` results are separate from this local run.
