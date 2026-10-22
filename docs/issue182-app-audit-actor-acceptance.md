# Issue 182 — application audit actor identifiers

Baseline `cfae14f2443af80da4647356b29995a32c1dc33d`; branch `codex/general-issue182-app-audit-actor-20261003`. Local implementation, not deployed.

AppRegistry now appends “操作人 ID：” and the exact `actor` from the existing audit response. Time, event, target and API ordering remain unchanged. A small `AuditEvent` type documents the fields consumed. Long IDs wrap within their audit row, including at390px. There is no name mapping, current-account marker, directory query, scope expansion, backend change or migration.

## Reproduce

```sh
cd apps/desktop-web
npm ci --offline --no-audit --no-fund
cd ../..
node scripts/verification/app-audit-actor/verify.mjs
```

Requires Google Chrome (or `CHROME_BIN`), Node and uv. The tracked runner loads the real AppRegistry React component through loopback Vite, starts this worktree's actual `xeonolab.desktop.create_app`, and uses temporary SQLite. The imported backend module path/hash is asserted and recorded. Three explicitly synthetic development human principals have desktop:read + desktop:admin. The harness switches its test bearer token to model sequential legal admin sessions; this is not an application feature or production identity.

A creates an app through the UI; B updates it through the same UI using its real current If-Match revision. Independent actual GET audit events and rendered rows must match exact actor/time/event/target/order. A third legitimate admin with a UUID updates the app; its actor has no name mapping and stays raw. No fabricated backend audit events are used.

Eight groups passed: actual empty audit; exact two-admin record match; no name mapping; failed read retains loaded records; successful retry; audit read/retry only GET; long UUID wrapping at390px; no directory endpoint requests. The 503 read failure is intentionally injected only by a browser route; empty and successful audit responses and all writes use the real local API. Existing failure message behavior is unchanged: a successful retry refreshes records but this patch does not introduce status-message clearing.

Output: `runtime/verification/app-audit-actor/browser-report.json`, `app-audit-two-admins.png`, `app-audit-390px.png` and logs. Both screenshots were visually inspected. Zero page errors. Temporary harness files and SQLite are removed in finally.

## Gates

- `uv run pytest`: 479 passed,19 warnings (includes concurrent temporary-directory cleanup warnings).
- `uv run ruff check xeonolab tests scripts/verification/app-audit-actor/local_api.py`: passed.
- `uv run python scripts/export_openapi.py`: passed,no schema diff.
- `npm test`:62 passed.
- `npm run build`:passed.
- Real Chrome helper:8 groups passed,0 page errors.
- `git diff --check`:passed.

AppRegistry source SHA256: `da0a583ff43f167e9490122bc36db8dd43d1f545bd72d62fe665de4cdf88e4ae`.

No new dependencies or production services are required. Rollback is reverting the small AppRegistry display patch. No deployment/production audit data was verified; existing production authentication/authority and availability remain unchanged dependencies. No remote writes, commit or push were performed by this agent.

Coordinator independently reran479 pytest/19warnings,62 frontend tests,Ruff,unchanged OpenAPI,build and eight actual local API/Chrome groups on the refreshed `f1b3e16` base. Both desktop and390px screenshots were visually inspected;0pageerrors. Source display hash remains unchanged.


Latest coordinator acceptance on base `e74301b03fab2cde4e50e395c02bc041862cde85`: 479 pytest/19 warnings on first parent run,62 frontend tests,Ruff,unchanged OpenAPI,build,eight actual local API/Chrome checks,0 page errors;390px screenshot inspected. Synthetic local data only; no production acceptance.


Latest coordinator acceptance on base `cfae14f2443af80da4647356b29995a32c1dc33d`: 479pytest19warnings firstpass62frontend/Ruff/OpenAPIunchanged/build/8actualAPIChrome0errors. The product source is unchanged from the frozen implementation. Synthetic local services/data only; no deployment or production acceptance.
