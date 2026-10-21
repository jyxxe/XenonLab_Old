# Issue 174: distinguish API key entries

Baseline: `f1b3e16e5011d3bfec326d5e931a557c34159b91`.

API key rows display their creation timestamp using the existing locale formatting and a stable eight-character ID suffix. Hovering the identifier exposes the full ID for verification. The suffix is a display abbreviation, not a globally unique identifier; revocation continues to send the full original ID. Active and revoked entries retain both identifiers. Key-row styles put metadata on its own line and keep the revoke button readable on compact screens.

The API/list/owner/scope/revocation behavior is unchanged. No tokens are added to list responses, shown in the list or persisted. The existing create-success one-time token UI is unchanged. The already merged #175 audit display remains intact; pending #173 account-context changes are independent and must be retained during integration.

## Verification

From repository root:

```sh
uv run pytest
uv run ruff check xeonolab tests
uv run python scripts/export_openapi.py
node scripts/verification/api-key-identifiers/verify.mjs
```

Run `npm test` and `npm run build` in `apps/desktop-web` after installing locked dependencies.

Results: 479 Python tests passed (19 existing warnings), 62 frontend tests passed; scoped Ruff, OpenAPI regeneration without output changes, frontend build and git diff check passed.

The browser runner renders the actual Accounts component in loopback Google Chrome with injected synthetic account/key API responses. A human owner manages its own keys, so no sibling/ancestor management is simulated. There are two active same-name/same-scope keys with different UUIDs/timestamps and one already revoked key. Eight checks passed with zero page errors: distinct creation times/short IDs, full-ID title, identifiers retained for revoked entries, failed revoke preserves rows and retry recovers, exact second-row DELETE changes only its state, stable identifiers/status after reload, no token creation/display/storage, and a readable 390px layout. Creation dates are asserted against the supplied timestamps. Reload is a fixture-only remount. Instrumented calls contain only existing GETs and the two self-owned DELETE attempts; no key/account creation or production write occurs.

Evidence: `runtime/verification/issue174-api-key-identifiers/browser-report.json`, `same-name-keys.png`, `keys-mobile.png`, browser and gate logs. Desktop and mobile screenshots were visually reviewed. This is a tracked manual runner, not an added CI job. Synthetic revoke errors and metadata validate UI behavior, not production account authorization or real key rotation.

## Delivery and rollback

No backend/schema/permission changes, new dependencies or migrations. No production deployment/acceptance performed. Rollback reverts the Accounts key-row display, its three CSS rules, and companion verification/doc artifacts; no data rollback is needed.

Coordinator refresh on `f1b3e16`: independently reran all gates and eight Chrome checks. The first backend run had one existing telemetry timing assertion failure (expected HTTP408, observed a closed connection); a full rerun passed479/19warnings. Both logs are preserved. The first manual browser invocation used the wrong working directory and failed before loading the fixture; the documented root invocation passed. No test or receiver behavior was changed.
