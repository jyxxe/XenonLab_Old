# Issue 195 — Fence stale Recent responses after loaded-row activation

Verified baseline: `865a72277008a5773236b50b993bacdecc78d6ab`, which includes the merged #194 keyboard Recent memory change.

`activateLoadedRow` now advances `rowNavigation.current` before its existing state updates and `rememberLoadedRow(rowId)`. Arrow navigation, Shift+Arrow, Enter, doubleclick and context-menu Inspector opening reuse this callback. An earlier Recent request still consumes its eventual response, but the existing generation check discards it. Mouse/table navigation retains its existing fence. Space, Ctrl/Meta+A and Escape retain their selection-only behavior.

## Reproduce and validate

```sh
npm ci --offline --no-audit --no-fund --prefix apps/desktop-web
EVIDENCE_CASE=current node scripts/verification/recent-row-navigation-fence/verify.mjs
```

The runner loads actual LibraryApp, TableView, Inspector, Recent and styles in headless Chrome against a fresh temporary XeLibrary FastAPI/SQLite service. A synthetic development human with read/write scope creates ordinary rows A/B and another table through the API. The fixture supplies the matching identity shape; it does not call the actual Desktop identity service. Success status, headers and body come from the real local backend and are held by a loopback Promise barrier. One failure case explicitly substitutes benign synthetic HTTP503.

Arrow/Shift cases start on A, begin the held Recent request, then activate B through the keyboard. The runner awaits browser response-body consumption and two animation frames before inspecting the final active row, Inspector and selection. It also verifies that keyboard/menu activation still remembers B in Recent on the actual merged #194 source.

Parent verification on this baseline passed ten groups: Arrow, Shift+Arrow, Enter, doubleclick, menu, mouse, obsolete failed Recent, normal Recent, selection-only keys, and a table switch. There were zero page errors. Evidence is in `runtime/verification/recent-row-keyboard-race/parent-current865/`, including the exact Library source hash and two screenshots.

An unfixed control overlays only the exact public LibraryApp source from baseline865a using `LIBRARY_COMPAT_SOURCE`, without editing product files. `EVIDENCE_CASE=parent-before865` deliberately exits1 at the first Arrow assertion: after the genuine old response is consumed, A overwrites B. The source is archived in `before865-source/LibraryApp.tsx` and the failure in `parent-before865.log`. Older unmerged-combination runs are historical evidence, not this final acceptance.

Parent gates:479 pytest passed with1 warning;73 frontend tests passed; build, scoped Ruff, OpenAPI export, helper syntax and diff checks passed. OpenAPI has no generated changes. Parent logs use `parent-*865.log`, `parent-pytest.log`, `parent-unit.log` and `parent-build.log`.

No API, scope, dependency, storage or migration change is introduced. This validation uses local synthetic data; production authentication, deployment and cross-device preference synchronization are outside its scope. Rollback removes the one generation increment while preserving the merged Recent memory callback.
