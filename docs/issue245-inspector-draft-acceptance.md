# Issue #245 — Confirm ordinary row switches with unsaved Inspector fields

Inspector continues to own its in-memory field draft and compute changed fields with the existing exact-JSON comparison. It exposes only the current table/row and dirty status to LibraryApp. An ordinary click on another loaded row now defers the existing selection handler and opens the shared accessible Modal. “继续编辑”, Escape, the close button and the backdrop all keep the source draft, current row and selection. “放弃修改并切换” applies the original ordinary-click selection to the clicked target. Same-row clicks, clean drafts (including reverted edits), and successfully saved drafts do not prompt.

Dirty status publication depends on the draft object as well as the current source. A row reset and its first edit may batch while the changed-field count remains one; publishing only when that count changes could leave a transient false status even while the visible draft is dirty. The regression performs six real pointer-click/input-fill B→A single-title edits before any additional field is changed, requiring confirmation and cancellation to keep each draft.

A pointer capture records the focused element and its text selection before the browser's row mousedown blurs the editor. Cancellation restores that focus and caret after the Modal's focus-trap cleanup. The pending request is keyed to its source table/row, navigation generation and auth epoch; a scope change or removed target invalidates it. No row mutation or selection/Recent update occurs while confirmation is pending. Inspector field normalization, changed-field PATCH, errors and conditional revisions use their existing implementation.

The scope is ordinary row clicks. Shift/Ctrl/Meta selection, checkbox selection, keyboard activation, doubleclick, context commands, table/view changes, Inspector close and window/identity lifecycles retain their existing behavior. This is not a general navigation guard or cross-record/session draft store. No autosave, API, ACL, permission or production dependency changes are introduced.

## Reproduce

```bash
node --test apps/desktop-web/tests/browser-acceptance-inspector-drafts.test.mjs
```

The authored regression uses the actual production main.tsx Library entry, TableView, Inspector and project styles in local Chrome. It starts the actual XeLibrary API on loopback with temporary SQLite, and creates its table and three rows through normal HTTP APIs. Only auth/session and users/me responses are synthetic fixture responses; library requests reach the real API with an explicit development principal. It reads no real accounts, credentials or production data and removes temporary data/server processes afterward. It is included in the existing browser-regression runner.

The final browser run checks:

- Clean, reverted, same-row and successful-save switching; dirty confirmation and explicit discard.
- All 13 schema field types, null source values, unsafe int64 and nested JSON, and invalid unsubmitted JSON/int64 text surviving cancellation.
- Active row, an existing multiple selection, the original Shift anchor, pointer-blurred focus and caret; shared Modal focus, Tab wrapping, Escape, header close and backdrop cancellation.
- Existing Ctrl/checkbox/keyboard behavior and ordinary discard selection.
- Zero browser PATCHes during cancel/discard/validation failures; API readback confirms A/B revision 1 before save. Explicit save alone sends a changed-field PATCH with If-Match revision 1 and preserves exact values.
- A real concurrent API edit advances A from revision 2 to 3. Both failed browser saves return 412 with the original If-Match revision 2; the source draft remains dirty and protected, and server readback retains the concurrent value. B stays revision 1.

A runtime-only exact `865a722` archive independently runs the negative-control branch: ordinary B click loses A's draft when returning to A, with no PATCH and both API revisions unchanged. The feature branch was then fast-forwarded from `865a722` to current integration `3b8661e6545510ac4290ad5fd0c0d7bac836c545` before final checks. Those baseline changes only synchronize pool tests; product and API sources are unchanged.

Final gate logs and browser report/screenshots are saved under runtime. The required pytest suite, frontend unit tests/build, scoped Ruff, unchanged OpenAPI export and whitespace check are recorded in `runtime/implementation-report.md`. Screenshots were visually inspected for the confirmation's clear actions/focus and the retained exact field values/conflict.

No production deployment, real OIDC, screen-reader or all-navigation acceptance is claimed. Rollback is reverting these frontend/test/document changes; no migration is required.

## Integration correction evidence

The first frozen candidate exposed an intermittent stale dirty-status handle under rapid single-field editing; combined attachment selection made it visible in a realistic flow. Independent actual Chrome evidence showed a matching source row/columns and visible changed title, but the previously published dirty flag remained false. This was an Inspector status-publication bug, not an attachment binding or API mutation failure. The reset's clean publication and next input can retain the same changed-field count; adding the draft object to hook dependencies fixes that publication.

Negative and one-line positive controls are retained under runtime/verification/issue245-draft-status-diagnosis. The pure one-line repair, without getters/console, passes the actual combined Library/picker/API scenario and the complete Inspector acceptance. A separate getter-only native Chrome diagnostic captures the false status after rapid B→A edit with no picker or tab change. Current authored regression uses native pointer and Playwright input fill; no DOM-event batching control is substituted for pointer acceptance. Final corrected hashes/gates are recorded in runtime/issue245-source-manifest.json and runtime/correction-report.md.
