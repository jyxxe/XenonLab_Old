# Issue #204 — Per-table field display order

Baseline: `f705776e60cfef881ac53dc6e6a48e3f77b956fc`. Branch: `codex/general-issue204-table-column-order-20261003`.

## Behavior

Table View exposes “调整字段列顺序”. The native disclosure and each 前移/后移 button are keyboard accessible. Adjustments immediately reorder schema field headers and their matching values. Row ID and 更新时间 remain before fields, 状态 remains after fields, and the selection checkbox remains fixed at the beginning. “恢复默认字段顺序” uses the current schema order.

The preference is local to this browser and table, matching existing local column-width preferences. The key is `xeonolab.library.column-order.{table.id}`, containing field names only. It is not account-scoped or synchronized across devices. Hydration only reads; explicit user edits write the current table key. State carries its owner table ID so a previous table's order cannot render or persist into the next table during hydration. Invalid JSON/shape uses schema defaults. Storage failures keep the current in-memory view but cannot guarantee survival across reloads.

`orderedColumns` preserves original schema objects, ignores unknown/removed/duplicate entries and appends new schema fields. `gridColumns` derives from this one displayed field list and continues to drive both headers and cells. Widths and sorting remain keyed by name, not position. No schema, API, data, scope, backend, dependencies or migration change.

## PR #148 integration

Anonymous Forgejo GET `/pulls/148/files` and the public TableView content at head `d983e08b9d4b12e4b87d7c93d1d6e113bf55a5ac` were inspected (responses under runtime evidence). Its `copySelected` builds both TSV headers and row cells from `gridColumns`, then appends status. When combining, retain this Issue's `displayColumns`-based `gridColumns` and PR148's existing serializer/copy operation. No second ordering contract is needed. This branch does not implement TSV copying or claim that PR148 is merged/deployed. Other independent row-activation/navigation changes (#191/PR194, #195/PR198) are not included.

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

Backend: **479 passed, 19 warnings**. Frontend: **66 passed**, including two meaningful pure order-resolution/movement tests; build passed. Scoped Ruff passed; OpenAPI export produced no tracked API diff. Node syntax and whitespace checks passed.

Chrome ran actual LibraryApp/TableView and actual styles with the repository's synthetic fixture. All identity/API responses are stubs. Its 100 seed rows yield 99 initially visible loaded records (the existing deleted record is excluded); explicit next page yields 100. The helper adds a second field to the second table and exposes the first table's synthetic schema to simulate schema refresh through normal table-opening routes. No real backend, OIDC, worker or production data is exercised. localStorage and all browser interactions are real. Temporary fixture files are removed on normal runner completion; no tracked fixture changes.

**10 checks passed, 0 page errors:**

1. actual keyboard four moves place files first; header/value binding, fixed system/status columns, Inspector and no API requests
2. same-table localStorage order survives actual browser reload
3. A/B/A/B switching preserves independent orders; no cross-table hydrate/persist write
4. schema removal ignores stale preference and new field appends with correct cell binding
5. keyboard default restore uses current schema, removes preference key and preserves other table preference
6. real pointer resize follows field name through reordering and persists in existing per-table width key
7. sorting, Arrow/Shift/Enter/Ctrl+A/Escape selection, Inspector, horizontal scrolling and bounded virtual rows remain functional
8. explicit row pagination retains display preference and renders appended row
9. 900px existing floating Inspector is explicitly closed; visible order controls remain keyboard reachable with immediate correct reordering
10. invalid stored preference falls back to schema without hydration rewriting storage or affecting another table

At 1440×900, files' header bounds were asserted entirely within the actual grid viewport while the Inspector remained open. Three screenshots were visually inspected: `files-first-with-inspector.png`, `ordered-columns-scroll-pagination.png`, `keyboard-order-narrow.png` under `runtime/verification/table-column-order/`. The 900px layout uses the existing floating Inspector; acceptance closes it through its real button before testing visible controls, not a hidden programmatic focus behind that panel. Reordering does not promise to eliminate total horizontal overflow.

Initial helper attempts had incorrect existing Inspector/button selectors, an obstructed narrow click, and assumed newly paginated rows stayed in the same virtual viewport. Only helper selectors/steps were corrected; final pagination waits for the actual loaded count and two animation frames, explicitly scrolls to the end and observes the appended row. The final runner uses actual Tab traversal to files' button, repeated native Enter presses and asserts retained focus during intermediate moves. No arbitrary sleep or fake completion assertion.

Source SHA-256:

- `src/library/TableView.tsx`: `2ef6d1a655bdc5ee6ea760b2768b25fb68106ec769fcdba6d3bc89ff46d876de`
- `src/library/logic.js`: `b1052af1f184fb58978ac8735fb0cc6e1ffb97ae44f8221c448d9f604c31baf2`
- `src/library/library.css`: `1d1a98eed607e4ac242b9379a3fac802dea346847924ee46a4f37558b5531ec9`

## Delivery / rollback

Local implementation is frozen for coordinator review; no commit, push, merge or deployment was performed by this agent. No new production dependency or migration. Rollback is the frontend change revert; the new local preference key can remain harmlessly unused. Existing PostgreSQL/Qdrant/model/OIDC production dependencies are outside this UI-only Issue and were not validated here.

## Coordinator independent acceptance

The coordinator independently reran the frozen source: 479 pytest passed / 19 warnings (13.33s), 66 frontend tests, build, scoped Ruff, OpenAPI export with no API diff, helper syntax and whitespace checks. The Chrome helper passed all 10 checks with zero page errors; all three screenshots were separately inspected. API/identity/schema fixtures remained synthetic. The 1440px check keeps Inspector open; the 900px check explicitly closes the existing floating Inspector. No production, OIDC or worker validation is claimed.

The coordinator rebased this branch onto the newly integrated PR203 (`f705776e60cfef881ac53dc6e6a48e3f77b956fc`). The preceding independent acceptance was on 2c348; the final integration gates and Chrome results are recorded separately below. PR203 is external work; this PR changes field display ordering only.

Final `f705776` coordinator gates: **479 pytest / 19 warnings (13.81s), 66 frontend tests**, build, Ruff, OpenAPI with no API diff, syntax and whitespace PASS. Chrome: **10 checks / 0 page errors**; three final screenshots independently reviewed. TableView, logic and column-order CSS source hashes above remain unchanged after rebase.
