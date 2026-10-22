# Issue 190 — Choose an already loaded move destination

Baseline: `cfae14f2443af80da4647356b29995a32c1dc33d`.

The existing move dialog now offers an optional native select labelled “已加载目录（可选）”. Candidates are unique paths and ancestors inferred from the current API-visible, loaded catalogue, including the existing sidebar root `/`. They are not a complete path inventory or a declaration of destination write access. Opening the picker does not load another catalogue page or query permissions. Explicit catalogue pagination adds its newly loaded candidates.

Selecting a candidate fills the existing target-path input. Manual paths remain supported; a custom path displays the select's placeholder without clearing the input. The existing move POST, current table-revision If-Match, success/error handling, catalogue refresh and metadata refresh are unchanged. The vector removal/reindex reminder remains visible. No backend, scope, schema, migration or dependency changes.

## Reproduce

```sh
npm ci --offline --no-audit --no-fund --prefix apps/desktop-web
node scripts/verification/move-path-picker/verify.mjs
```

The runner uses actual LibraryApp and project styles, Chrome and a temporary local XeLibrary FastAPI/SQLite instance imported from this worktree. It creates 103 owned synthetic tables with nested paths. Explicit development human tokens are locally configured; the fixture supplies the matching identity shape because this service is not the desktop identity endpoint. All catalogue/move/readback responses and the stale-revision rejection come from the real local backend. No production service or credential files are used.

Eight assertions cover the exact first-page candidate set and uniqueness, no implicit pagination/permission requests, actual ArrowDown/Enter keyboard selection and value synchronization, real move with current If-Match and stable table ID, manual new-path persistence, real 412 retaining the target input, successful retry after ordinary metadata refresh, explicit pagination and narrow-screen reachability, and a read-only identity's disabled move control with GET-only UI requests. The browser report contains the concrete paths, calls, source SHA-256 and zero page errors. Native selection by value is also used for deterministic target setup; the runner does not claim an external OS application interaction.

Evidence: `runtime/verification/move-path-picker/browser-report.json`, `loaded-target.png`, `conflict-retains-input.png`, `narrow-picker.png`, plus actual command logs. Screenshots were visually inspected for separated labels, readable scope/reindex reminders, retained conflict input and usable narrow-screen controls.

Validation: `uv run pytest` — 479 passed (19 warnings); `uv run ruff check xeonolab tests` passed; `uv run python scripts/export_openapi.py` passed with no generated schema change; frontend `npm test` — 62 passed; `npm run build` passed; `git diff --check` passed. Browser eight groups passed with zero page errors.

This validates local synthetic UI/API behavior, not production deployment, real-user frequency, all ACL combinations or vector worker completion. Existing backend destination authorization remains authoritative. Rollback is the frontend change only; no data migration is required.


Coordinator independently reran the frozen source:479 pytest passed/19warnings on first run,62 frontend tests,Ruff,OpenAPI unchanged,build,node syntax check,diff check,and all8 actual local API/Chrome groups with0pageerrors. Desktop and390px picker screenshots visually inspected. Source SHA256 remains b718d26d8f64873ce0f0b770fec48eea607cdf263bb1e3018286da890089e94c. No production acceptance or deployment.
