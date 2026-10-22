# Issue 218: open deleted Recent records, including later pages

Implementation baseline: `eecec3ec4e171b7536681bf872b5898faf7ba25f`; final integration onto release base `7d216a08e3917cea08d87d7206209f0299d08253` on 2026-10-22 is recorded at the end of this document.

Recent activates an exact deleted row from the current authenticated include_deleted list when it is already returned. The table-session open function returns that page only after its existing generation check. For a target outside the first 100 records, the ordinary detail read remains first; only its `XL_ROW_NOT_FOUND` response permits one precise `?include_deleted=true` fallback. There is no cursor-page scan. Navigation, API/subject/authEpoch changes and unmount invalidate obsolete activation or fallback requests. Active targets keep their ordinary detail read.

The existing GET `/api/v1/tables/{tid}/rows/{rid}` gains optional boolean `include_deleted`, default false. The same read scope and row ACL authorize both modes; default listing/detail/search deletion filters remain unchanged. The export adds only this query parameter (10 lines). A meaningful API test checks default/explicit false exclusion, opt-in row/ETag, existing row ACL precedence and absent targets. No new runtime dependency or migration.

## Reproduce

```sh
SOURCE_CASE=complete node scripts/verification/recent-deleted-record/verify.mjs
SOURCE_CASE=before node scripts/verification/recent-deleted-record/verify.mjs
uv run pytest
uv run ruff check xeonolab tests scripts/verification/recent-deleted-record/local_xelibrary_api.py scripts/verification/recent-deleted-record/local_desktop_api.py
uv run python scripts/export_openapi.py
npm test --prefix apps/desktop-web
npm run build --prefix apps/desktop-web
```

The helper renders actual LibraryApp/Sidebar/TableView/Inspector/SearchView and project styles against separate actual temporary XeLibrary and Desktop API factories. Records are API-created, lawful synthetic data; the development human identity is explicit and its identity response is from the local Desktop API. No production/OIDC/provider/worker acceptance is claimed. Barriers buffer genuine response bodies and wait for consumption plus two animation frames.

Twelve complete browser checks passed with zero page errors: deleted Recent activation and History restore; default deletion filtering; active detail200 and absent detail404; stale list responses after Search/table changes; pending deleted-page activation; a genuine 101-record later-page target restored from revision2 to3 with one first-page read and one precise fallback, no cursor scan; late precise response suppression; late default404 suppression before fallback; and authEpoch refresh invalidation. A first-page deleted target restores r4 to active r5 with values/history intact.

Initial first-page-only implementation evidence remains under initial-current (eight checks). The independent pagination-probe captures its omitted 101st target and false not-found notice. Complete evidence is under complete (twelve checks); reports bind source hashes. The before command overrides only LibraryApp/hooks with exact eecec3 source and intentionally fails selected-row activation after genuine list200/detail404; it is a frontend regression control, not a whole historical backend replay.

## Honest boundaries

Restoring a later-page target succeeds in the API, but existing restore refresh loads only the first 100 records and can remove that target from the client rows/Inspector selection. The selected-deleted/History screenshot is taken before restore; the API trace and independent Issue #229 replay record the remaining selection loss. Users can reload later pages/reselect or reopen Recent. This separate existing refresh defect is assigned to independent Bug audit; this PR does not claim it solved. Deleted-row attachment listing also retains its existing 404 in Overview; record activation and History are usable.

An initial helper purge used incorrect If-Match and returned412. Its full original log was overwritten; only a tool-output transcript survives. The final runner records the ordinary wrong-header412 negative and then uses revision:<n>. A transient restore-message wait was replaced with persistent API revision/History assertions because Inspector clears that message on row refresh; no product behavior was changed for either fixture assumption.

Child gates on eecec3: 480 pytest/19 warnings, 67 frontend tests, build, scoped Ruff including both helpers, node syntax/whitespace and OpenAPI export passed. Exact API diff is the optional GET query parameter. The coordinator verified the complete report and unchanged frontend hashes, confirmed the owned browser runner had ended, and took over final documentation/review. No child commit/push/remote write occurred.

The latest baseline includes #194 remembering; its activateLoadedRow behavior is preserved. #198 keyboard activation fencing remains separate integration work. Rollback reverts this frontend patch and optional query parameter together. Old API clients retain their default filtering; the frontend/backend must be released together for the new precise fallback. No deployment by this Harness.


## Coordinator acceptance on current integration

Final baseline: `743c5f3e9a184d74466a7d26c1aa1347234ad447` (includes merged #194). Conflict-free rebase preserved its keyboard/menu Recent behavior. `uv run pytest`: 480 passed, one existing Starlette deprecation warning, 13.10s. Frontend unit tests: 69 passed, zero failures. Frontend build, scoped Ruff including both API helpers, OpenAPI regeneration, Node syntax and whitespace checks passed. OpenAPI export retains exactly the reviewed 10-line optional-query change. Actual local API/Chrome replay `SOURCE_CASE=parent-743c` passed all 12 checks with zero page errors; report under `runtime/verification/recent-deleted-record/parent-743c/` binds all three frontend files and backend source hashes. The coordinator visually reviewed selected deleted target/History, ordinary History and immediate-delete screenshots. These screenshots demonstrate activation before restoration; restore persistence is established by actual API trace assertions.

The pre-rebase coordinator run on 03fb also passed 480 pytest (one warning), 69 frontend tests and all 12 actual API browser checks; its evidence is retained under parent-03fb. The independent later-page post-restore selection bug has now been actually published as Issue #229 after a separate coordinator Chrome/API replay and three-screen visual review. It remains outside this change. No production write/deployment or actual OIDC/provider/worker verification was performed.


## Coordinator refresh after maintainer review

Maintainer comment 1752 requested refresh after #224 advanced the integration branch. Current acceptance baseline: `865a72277008a5773236b50b993bacdecc78d6ab`. Two import conflicts in LibraryApp were resolved by retaining both the int64 JSON helpers/changedFields and this change’s APIRequestError/loadedDeletedRow; no behavior from #224 was removed. The reviewed patch still consists of the same 14 files and optional OpenAPI query definition. Final rerun passed 480 pytest (one existing Starlette warning, 14.61s), 74 frontend unit tests (including merged int64 coverage), build, Ruff, OpenAPI regeneration, Node syntax and whitespace checks. Actual temporary API/Chrome replay parent-865a passed all 12 checks with zero page errors; all four source hashes match this checkout, and selected later-page/History plus immediate-deletion screenshots were visually checked. Earlier baseline evidence remains preserved separately. Rollback still requires reverting frontend and optional backend query together; Issue #229 and #198 remain separate work. No deployment by this Harness.

## 2026-10-22 final integration onto release base `7d216a0`

The branch was merged with the current release base `7d216a08e3917cea08d87d7206209f0299d08253`, which already contains the deployed #229/#240 refresh fix, #157 mutation-context fence, #210 column order, #207 storage tab and #102 work API. Conflicts were resolved by keeping both sides:

- `LibraryApp.tsx` (three hunks): kept the base `useLayoutEffect` search-scroll restoration, `createRequestLifetime`, `filterRows`/`rowSelectionRange`/`sortRows` imports and search refs, together with this change's `APIRequestError`/`loadedDeletedRow` imports and `rowOwner` unmount invalidation. The merged `refresh(showDeleted, retainedRow, isCurrent)` from #240 and this change's Recent activation / precise fallback logic are both retained.
- `logic.js` / `logic.d.ts`: union of `loadedDeletedRow` with the base `retainRefreshedRow` and column-order helpers.
- `library-logic.test.mjs`: both test groups kept.
- `tests/test_library.py`: the base's newer tests and this change's `include_deleted`/ACL test kept.

Helper adaptations (recorded honestly; product assertions unchanged):
- The "ordinary Refresh restores default deleted-row exclusion" step assumed the session `includeDeleted` flag resets when re-opening the table from the tree. Since the original productisation the base intentionally keeps `openTable` passing the current `session.includeDeleted`, so after a delete/restore flow a toolbar Refresh still includes deleted rows. That is pre-existing product behavior unrelated to #218; the helper now asserts the default exclusion on a genuine fresh page load (real reload) instead of relying on the racy in-session transition.
- `CHROME_BIN` is honored so the helper runs on this VM and in CI.

The "Honest boundaries" section above recorded that restoring a later-page target lost the selection because the old refresh loaded only page 1. That defect was subsequently fixed and deployed as #229/PR #240; the current base contains the retained-row refresh, so that boundary no longer applies to this integration.

Final gates on `7d216a0`: **522 pytest passed** (1 warning), **113 frontend unit tests**, `tsc --noEmit`, production build, Ruff and OpenAPI regeneration (no extra diff) passed; the actual local XeLibrary/Desktop API + Chrome replay passed **all 12 checks with zero page errors** (evidence regenerated under `runtime/verification/recent-deleted-record/current/`). CI and the protected preflight on the exact integration head remain the merge gate. No production write/deployment by this Harness.
