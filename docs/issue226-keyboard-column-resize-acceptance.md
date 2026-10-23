# Issue 226: keyboard column width adjustment

Baseline: `865a72277008a5773236b50b993bacdecc78d6ab`.

Every existing adjustable system/schema column separator is now focusable in header order. Its column name, vertical orientation, pixel value and 80–520 range are exposed through ARIA; focus has a visible outline. Left/Right change the preference by10px, Shift changes it by40px, Home selects80 and End520. A tooltip explains these keys. Unset preferences begin at the measured header size. After an explicit edit, the exposed value is the pixel width preference used by the existing width/min-width styles; normal table layout/content constraints remain unchanged.

Pointer and keyboard changes share the same clamp/update/storage function. Widths retain their existing per-table localStorage key and column.name identity. Pointer listeners are cleaned up at table change, cancellation and unmount. Separator key events stay inside the control, so they do not trigger grid row activation or selection shortcuts; Tab proceeds normally. Sorting, schema/column order and backend behavior remain unchanged. No new API, scope, dependencies or migrations.

## Repeatable actual local API/Chrome acceptance

From repository root, with locked dependencies and system Chrome available:

```sh
SOURCE_CASE=before node scripts/verification/keyboard-column-resize/acceptance.mjs
node scripts/verification/keyboard-column-resize/acceptance.mjs
```

The helper mounts actual LibraryApp/TableView/Inspector and styles. Actual XeLibrary and Desktop API factories run in fresh temporary directories on loopback, using an explicitly configured synthetic `resize-local-admin` development actor. Two tables and35 ordinary synthetic rows per table are created through normal local APIs. Identity reads use the actual local Desktop API. This is not production/OIDC or a real administrator dataset.

The before mode loads the exact unmodified865a TableView through a test-only Vite plugin: Tab from score sorting skips its separator, which has no tabindex, while pointer dragging works. Report/screenshot and effective TableView hash are under runtime/verification/keyboard-column-resize/before. It is a successful defect-control observation, not a failed acceptance run.

The current mode passes10 checks with zero page errors:

1. Tab from score sorting reaches its resize handle.
2. All four adjustable system/schema columns have ordered Tab reachability, column names, vertical orientation and valid ARIA ranges/value text.
3. Unset width starts at the actual header measurement;10/40px key steps work.
4. Home/End and repeated arrows clamp80–520 and update ARIA.
5. Handle ArrowUp/Down, Enter, Escape and Ctrl+A do not activate/select grid rows.
6. Pointer drag shares persistence and ARIA updates with keyboard edits.
7. Pointer extremes use the same80–520 bounds.
8. Reload restores the same table's width; another table has independent storage and switching back restores the first table.
9. Grid arrows/Escape/Ctrl+A continue to work when the grid itself has focus.
10. Sorting and virtualized scrolling preserve exact row-ID/field-value binding against actual API readback.

Final report: runtime/verification/keyboard-column-resize/current/browser-report.json, with actual source hashes. Final log: final-complete.log. Visual checks: current/keyboard-focus-pointer.png shows a visible focused separator and unchanged record Inspector; current/sorted-virtual-table.png shows sorted numeric values with their corresponding labels. Both screenshots were inspected. Initial helper failures were expectation mistakes (badge text whitespace and aria-hidden sort arrow excluded from the accessible name); current.log/current-final.log retain them. No product change was made to satisfy those mistaken assertions.

## Gates and integration boundaries

- `uv run pytest`:479 passed,1 warning,13.65s (pytest.log).
- `uv run ruff check xeonolab tests scripts/verification/keyboard-column-resize/*.py`:passed (ruff.log).
- `uv run python scripts/export_openapi.py`:passed, no generated API diff (openapi.log).
- `npm test` in apps/desktop-web:74 passed (unit.log).
- `npm run build`:passed (build.log).
- Node syntax and git diff --check:passed.

PR210's pending per-table column-order work uses the same column.name preference identity; this patch does not duplicate it. PR148's pending TSV feature and independent row navigation work are not implemented. The existing client width storage remains a local per-table preference, without new cross-device/account synchronization claims.

No real screen-reader or external assistive-device testing was performed. No production deployment is claimed. Rollback is reverting this frontend change; there are no new production dependencies or migrations.

Independent parent acceptance on the same frozen source passed all10 Chrome checks with zero page errors and matching TableView/logic/CSS hashes under `parent-review/`. The exact865a TableView control was also independently replayed (`parent-before.log`, `before/`); original child control remains in `child-before/`. Parent gates:479pytest/1warning14.67s,74frontend tests/build, scopedRuff, unchanged OpenAPI export, helper syntax and diff checks. Parent screenshots show the focused separator and exact sorted field/value binding after virtualized scrolling. These tests use actual local Desktop/XeLibrary APIs with synthetic data, without production or assistive-device claims.
