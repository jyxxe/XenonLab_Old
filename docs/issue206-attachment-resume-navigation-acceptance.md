# Issue #206 — Recover multipart uploads across record navigation

Baseline `eecec3ec4e171b7536681bf872b5898faf7ba25f`; branch `codex/general-issue206-attachment-resume-navigation-20261003`.

## Behavior and ownership

A LibraryApp instance owns one in-memory upload store per `subject` / `authEpoch`. Both Inspector entry points (overview and attachments tab) receive that same store. Keys are exact JSON tuples `[tableId, rowId]`; unfinished work keeps its original File, upload ID, next part, part size/count, progress, busy and recovery message. Switching records, tables or Inspector tabs may unmount the panel, but returning to the same record recovers its controls and filename. Another record does not receive that File or session.

The busy flag is checked synchronously in the shared store before retry/cancel starts, so remounting cannot start a second operation on the same upload. A completed request cannot clear a newly started upload because the original operation retains busy through its final cleanup; no new operation can enter before that cleanup. After completion/cancellation, the mounted panel refreshes its own list. Its follow-up callback also compares the exact terminal snapshot, so a later operation supersedes an older refresh error. List request generation checks remain in place, and list refresh does not clear the independently stored resumable error. Other attachment-operation feedback takes priority over past upload messages.

Completed/successfully cancelled entries contain no File and are deleted when their last panel unsubscribes. Already-unmounted terminal entries are deleted immediately; visited rows do not become an unlimited history. Only unfinished/in-flight entries survive navigation. This is an instance-local cache, not a global cache or persistent File store. Standalone Attachments callers may supply a store; without one their state remains local to that component lifetime.

Changing subject/authEpoch or unmounting the Library disposes the old store. Old responses cannot repopulate it or dispatch subsequent parts/complete/refresh through the disposed owner. This does **not** abort a request already sent, cancel server sessions automatically, or provide browser-refresh/closed-window recovery. An unfinished server session may remain after identity/window teardown, governed by existing backend expiry/quota/cancel semantics (24-hour expiry in the current contract). Cancellation while still in the owning instance uses the existing authenticated DELETE API. No backend route, scope, schema, migration or dependency change.

Small uploads retain their existing form-data path. Failure to create a large upload has no phantom session and offers selecting the file again. Failed continuation/cancellation keeps recovery controls. Successful multipart completion uses the upload ID already bound server-side to its creator/table/row; there is no new client-side bind to the current selection.

## Reproduce validation

```sh
npm ci --offline --no-audit --no-fund --prefix apps/desktop-web
uv run pytest
uv run ruff check xeonolab tests scripts/verification/attachment-resume-navigation/inspect_sessions.py
uv run python scripts/export_openapi.py
npm test --prefix apps/desktop-web
npm run build --prefix apps/desktop-web
node --check scripts/verification/attachment-resume-navigation/verify.mjs
node scripts/verification/attachment-resume-navigation/verify.mjs
git diff --check
```

Child acceptance on the original f705776 baseline: **479 passed, 19 warnings, 14.43s** (initial run also 479/19,15.14s). Frontend: **66 passed**, including two new pure upload-store tests for cross-table same-row identity, surviving unsubscribe, terminal cleanup and disposal/late-write isolation. Frontend build, scoped Ruff, Node syntax and whitespace checks passed. OpenAPI export passed with no API diff.

### Chrome/API provenance

The helper runs actual LibraryApp/Inspector/Attachments and actual styles in local headless Chrome. Its Vite proxy connects browser upload/list/bind/unbind/download requests to the actual XeLibrary FastAPI factory with a fresh temporary SQLite/object directory. Setup uses an explicit synthetic development principal with admin/read/write/attach; browser requests use a separate token of that same subject with only read/write/attach, matching the synthetic identity response. The identity shape, switch button and development tokens are test fixtures; there is no OIDC or production session. Tables/three rows, ordinary 17 MiB binary files and 2 KiB small file are synthetic lawful test data.

Only designated second-part failures, upload-create failure and cancel failure are explicitly simulated HTTP503. First successful part, retry parts, completion, DELETE cancellation, row attachment lists, normal bind errors and downloaded bytes are genuine local API responses. Successful part barriers buffer the genuine response status/headers/body, prove it is ready but unsent, then release it. Identity-switch verification waits for the **exact old upload's** part-2 raw response to be observed, then two animation frames before checking no part3/complete was sent. This is one browser-to-real-local-API integration test, not separate stub-browser and API tests misrepresented as end-to-end production.

**10 checks passed, 0 page errors:**

1. real multipart session/first part + three explicit local second-part failures show recoverable state
2. multiple keyboard A/B/A, overview/attachments/fields mounts and cross-table return retain same session and error without leaking to another row
3. retry failing three more times preserves original upload session/cursor and recoverable controls
4. cancel failure remains recoverable; retry DELETE genuinely marks server SQLite cancelled and creates no attachment
5. failed session creation has no resume phantom and allows selecting a new file
6. genuine pending retry survives row/tab switches with both actions disabled; completes once and refreshes remounted original record only
7. ordinary small multipart-form upload still links/refreshes without creating resumable state
8. existing authenticated download retrieves exact ordinary small-file bytes after upload completion
9. existing unlink removes metadata; ordinary rebind of removed ID reports real 404, valid same-row bind remains idempotent, and current feedback supersedes upload success
10. authEpoch/subject switch clears old File/session UI and stops subsequent parts/complete after a genuine old response; server session remains incomplete, not auto-cancelled

`inspect_sessions.py` reads only the isolated acceptance `uploads.sqlite`. Final recorded state: cancel-me.bin `completed=0,cancelled=1`; complete-once.bin `completed=1,cancelled=0`; owner-change.bin `completed=0,cancelled=0`. No session is created for start-fails.bin. Completed large attachment is counted once on its original row and zero on the other row/table; only one complete call is made. The later valid same-row bind is idempotent. Normal unlink deletes metadata, so attempting to rebind its removed ID correctly reports the existing 404; this is a normal UI error-feedback check, not an access bypass experiment. Downloaded small-file bytes are checked exactly.

Two screenshots under `runtime/verification/attachment-resume-navigation/` were visually inspected: `resume-after-record-tab-table-navigation.png` (correct original row, original filename, 33% progress and both recovery actions after navigation), `retry-completed-original-record-once.png` (one 17 MiB attachment on the same original row, no recovery controls). Initial helper assumptions about randomly generated row-ID order and re-binding deleted metadata were corrected from actual API source; only test steps changed for those corrections.

Source SHA-256:

- `src/Attachments.tsx`: `901a905c0f5a26d6edecebf9b7e3ce8466312aa9b249448ecdf25920035b67c4`
- `src/upload-state.js`: `3122fb6159d290bf9c56a93b4c4f6a9b8a6ee8227073f5cacdb4d2da0ab77412`
- `src/library/Inspector.tsx`: `fad7da6268405a49da06f0713525151af5d11395a653e5c1400efb2f692e64c8`
- `src/library/LibraryApp.tsx`: `d0ee7f4feb983cec26d08d8a2cc1bee2254f1ef4c3d97283d25b291657d90345`

## Delivery and limits

Read fresh Issue206/comments and full open PR pages `[33,0]`; no open PR covered this lifecycle. Independent #189/#194/#198/#210 features were not added. Source is frozen for coordinator review; no commit/push/remote write/deployment by the child agent. No migration/new production dependency. Rollback is reverting these frontend changes (no persistent preference to migrate). Production TLS/OIDC/network failures, real resource quotas under load and background cancellation are not validated. Existing storage/authority/model production dependencies remain outside this local UI lifecycle fix.

## Coordinator independent frozen-source acceptance

On f705776, the coordinator independently reran 479 pytest / 19 warnings (15.65s), 66 frontend tests, build, scoped Ruff including the helper, unchanged OpenAPI export, syntax and whitespace checks. The real local API/Chrome integration helper passed all 10 checks with zero page errors. Two screenshots were separately inspected, and all delivered files/evidence passed an own-credential byte scan. Simulated failures, synthetic identity shape and real local response/SQLite boundaries remain as documented above; no production validation is claimed.

The coordinator rebased this source without conflicts onto newly integrated PR209 (`eecec3ec4e171b7536681bf872b5898faf7ba25f`). Its Work/Dashboard/Forgejo link changes are external work and are not this PR’s scope. The previous frozen-source acceptance remains recorded on f705776; final gates on eecec3 are recorded below.

### Final acceptance on integrated eecec3

The coordinator reran the frozen product source after rebase: **479 pytest passed, 19 warnings (81.11s)**; **68 frontend unit tests passed** (including the integrated baseline’s two link tests); frontend build, scoped Ruff, unchanged OpenAPI export, Node syntax and whitespace checks passed. The real local XeLibrary API/Chrome helper again passed **10 checks, zero page errors**. Both final screenshots were separately inspected. The existing `npm run test:browser-regression --prefix apps/desktop-web` passed **10/10, zero failed/skipped** in 54.707 seconds; those existing workflows use their documented synthetic fixtures.

The first post-rebase helper attempt timed out at the initial 17 MiB `setInputFiles` operation after 12 seconds, before upload assertions. Its log is retained as `parent-eecec3-browser-initial-input-timeout.log`. A fixture-only change gives file selection an explicit bounded 60-second timeout; the same 17 MiB real-API scenario then passed all checks. Product source hashes above are unchanged. Final evidence is under `runtime/verification/attachment-resume-navigation/parent-eecec3-*`; no production service or OIDC validation is implied.
