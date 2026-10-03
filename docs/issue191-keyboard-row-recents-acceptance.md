# Issue 191 — Remember loaded row activations

Baseline: `03fb8985bb18d7c3ae9a4a965c34f164907226a8`.

LibraryApp now shares `rememberLoadedRow` between mouse selection and the existing TableView activation callback. The helper only remembers a row whose ID matches a currently loaded session row and whose table is present. The ID, title, timestamp and `prefs.remember` contract remain the same. Activation preserves the existing Inspector overview/open behavior; mouse selection keeps its existing Inspector tab and selection logic.

TableView already routes ArrowUp/ArrowDown (including Shift extension), Enter, doubleclick and “在 Inspector 中打开” through its activation callback. No TableView selection handling is changed. Space, Ctrl/Meta+A and Escape remain selection-only actions. Mouse Shift/Ctrl/Meta semantics remain unchanged. The helper adds no row, directory or preference request. Inspector overview's existing attachment GETs still occur.

## Local verification

```sh
npm ci --offline --no-audit --no-fund --prefix apps/desktop-web
node scripts/verification/keyboard-row-recents/verify.mjs
```

The fixture imports the actual LibraryApp, TableView, Inspector, sidebar and project styles. A temporary real XeLibrary FastAPI service and SQLite tables are created in development mode, using two synthetic independent human owners with read/write scopes only. Each owner creates its own table and four ordinary rows through its own lawful token. The fixture supplies matching `/users/me` identity shapes, since XeLibrary does not host the desktop identity endpoint. Resource responses and owner-visible catalogues come from the real backend. No production data, identity files or credentials are used.

Nine browser groups verify:

- ArrowDown/Shift+ArrowDown active row, visible-order selection, Inspector field value and exact Recent ID; only existing attachment reads are triggered.
- Enter keeps one Recent entry and selection; ArrowUp remembers the active row.
- Space, Ctrl+A and Escape change selection without changing Recent.
- Doubleclick produces one exact Recent entry.
- Context-menu Inspector opening records its loaded row.
- Mouse, Shift range and Ctrl toggle retain selection and Recent behavior.
- A Recent entry returns through the existing row-navigation API to the exact row.
- Browser reload preserves the same subject's local cache.
- Identity switch exposes each owner's lawful catalogue and its own cache, preserves the other cache, and restores the previous Recent entry on return; clicking it returns to that owner's row.

Evidence lives in `runtime/verification/keyboard-row-recents`: browser report with calls/source hash, three screenshots, and actual gate logs. Screenshots were visually inspected for active/selected rows, matching Inspector content and sidebar Recent entries. Final browser run: nine groups passed, zero page errors.

`uv run pytest`: 479 passed, 19 warnings. Scoped Ruff and OpenAPI export passed with no generated schema change. Frontend tests: 62 passed; build passed. Helper syntax and `git diff --check` passed.

## Service boundary and rollback

Current `useLibraryPrefs` writes subject-scoped localStorage. This is the existing storage mechanism, not a DesktopPrefs outage fallback. The cross-device GET/PATCH implementation proposed by PR #111 remains unmerged and is not integrated here. Browser reload/account cache isolation does not prove cross-device persistence. Production deployment and real-user frequency have not been verified.

No new endpoint, backend, scope, dependency or migration. Reverting the frontend callback change rolls back this fix; existing local cache entries can remain.


Latest coordinator acceptance on base `cfae14f2443af80da4647356b29995a32c1dc33d`: 479pytest19warnings firstpass62frontend/Ruff/OpenAPIunchanged/build/9actualAPIChrome0errors/keyboardownerAscreensreviewed. The product source is unchanged from the frozen implementation. Synthetic local services/data only; no deployment or production acceptance.


Coordinator acceptance on current integration base `2c3481d96f8ba602be5418a44882ba0aa25187db`: 479pytest19warnings firstpass64frontend/build/Ruff/OpenAPIunchanged/9actualAPIChrome0errors/existingownerlocalStorage. The base includes the already merged #190 move picker and #193 filter tree; this Issue’s functional additions remain scoped. Synthetic local data/services only; no production action by this Harness.


Coordinator refresh requested by maintainer comment1606: replayed without conflicts onto integrated `eecec3ec4e171b7536681bf872b5898faf7ba25f`. Final independent gates: **479 pytest passed, 19 warnings (16.50s)**; **66 frontend unit tests passed**, build, scoped Ruff, unchanged OpenAPI export, helper syntax and whitespace passed. Actual local XeLibrary API/Chrome passed **nine groups, zero page errors**; all three final screenshots were inspected for exact active/selected rows, Inspector values and Recent entries. Final LibraryApp SHA256 is `06515b0669065cbcd6f12b8ec4e6f9c6a604e1b7d28fc27cb9d7709cb344a451`. Evidence is `runtime/verification/keyboard-row-recents/parent-eecec3-*`; helper baseline metadata was updated, no additional product change. Unmerged #198 remains an independent activation fence: if integrated later, its rowNavigation increment must precede this shared activation helper without removing rememberLoadedRow. No cross-device persistence, production or OIDC acceptance is claimed.


Before publishing this refresh, #220 was externally integrated as `03fb8985bb18d7c3ae9a4a965c34f164907226a8`. The coordinator replayed this branch without conflicts onto that current tip, preserving the upload store and Inspector wiring. Final gates on 03fb: **479 pytest passed, 19 warnings (13.59s)**; **68 frontend unit tests passed**; build/scoped Ruff/unchanged OpenAPI/helper syntax/whitespace passed. All **nine actual local API/Chrome groups passed, zero page errors**; all three final screenshots separately inspected. Final LibraryApp SHA256: `0f8e775b873a9edee046fe8f8ee072252f42072bbbebd64044974d70a30ea3fd`. Earlier eecec3 screenshots/report remain in `parent-eecec3-evidence/`; final logs are `parent-03fb-*`. Only synthetic local identities/data are used; no production action.
