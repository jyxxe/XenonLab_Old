# #305 / #313: desktop discovery migration

Dock, LaunchPad, shortcuts, restored windows and builtin/plugin widgets now use
`GET /api/v1/capabilities` as their availability source. The existing `/apps` and
`/users/me/plugin-widgets` APIs still provide iframe descriptors; native adapters
remain statically linked trusted components. No arbitrary code is loaded.

## Discovery projection

- Builtin UI ids `builtin.platform.app.<desktop-id>` and
  `builtin.platform.widget.<desktop-id>` select existing native adapters.
- Enabled installed plugin UI capabilities include `desktop_id` to join the
  account's accepted app/widget descriptor, plus the existing `plugin_id`.
- Administrator-managed apps are projected as
  `registered.platform.app.<desktop-id>`, source `registered`, version `1.0.0`
  (the descriptor-adapter contract version, not an external app's release).
- The frontend accepts enabled UI capabilities only, checks required scopes,
  and requires plugin ownership and desktop ids to match descriptors. Missing
  capabilities cannot be restored by stale descriptors or widget preferences.
- Unknown native ids have no local adapter and are ignored. Registry entries
  cannot substitute for a builtin withheld by discovery. Chat/openwebui keeps
  its existing configured-origin alias handling.

The builtin manifest covers all ten existing app adapters and seven builtin
widgets. The Embedding *app* requires `desktop:read`, matching existing access to
its permission-specific empty states; embedding *provider* capabilities and
protected backend operations retain their `embedding:read` gates. Work/search
widgets retain their existing unavailable states rather than acquiring scopes.

## Compatibility and lifecycle

There is no database migration or change to legacy descriptor response shapes,
iframe URLs, sandbox flags, permissions, layout ids, Dock preferences or widget
preferences. Initial discovery failure shows the existing retryable desktop
load error and never silently falls back to hardcoded availability. A successful
plugin enable/disable refresh removes unavailable windows, Dock entries and
widgets. Re-enable restores preference-selected widgets; closed windows are not
reopened automatically. Already loaded apps are reconciled on refresh, not by
server push. Direct app/widget APIs remain responsible for server authorization;
capability metadata is not a plugin execution gateway (resolution is PR #318).

Deploy the backend projection and desktop bundle together. Rollback reverts both
parts of this commit; persisted state and installed snapshots need no conversion.
PR #317 separately supplies the declarative knowledge migration example; this
change makes the existing knowledge native adapter discoverable at runtime.

## Acceptance evidence

- Backend synthetic integration: builtin adapter coverage, plugin app/widget
  descriptor joins, disable filtering, registered app permissions and deletion.
- Frontend unit tests cover enabled/removed/disabled entries, required scopes,
  runtime tiers, ownership mismatch and malformed discovery.
- Real headless Chrome renders the actual DesktopShell and settings components
  with synthetic data and intercepted plugin iframe content. It opens a plugin
  from LaunchPad, verifies sandbox flags, disables it, observes window/widget
  removal despite deliberately stale descriptor responses, re-enables it, and
  checks discoverability returns. Registry failure shows the desktop load error.
  Screenshot reviewed at 1440x1000; no page errors. No real plugin, account,
  production session or production deployment was used.
- Existing shell/session/smoke browser tests and Work-widget links are rerun to
  cover native knowledge, persisted layout migration, account-session lifetimes,
  original widget behavior and basic API error recovery.

### Local command results

Python 3.12.14 with the repository dependency versions (TLS-verified HTTPS mirror
used during environment preparation; no dependency/lockfile changes):

- `uv run --no-sync pytest tests/test_capability_desktop.py tests/test_capabilities.py tests/test_plugins.py`: 24 passed.
- `uv run --no-sync pytest --basetemp runtime/test-tmp`: 726 passed, 13 failed.
  The same 13 Lean verifier tests fail on the unchanged c44076a baseline. This
  desktop account gets `PermissionError 13` statting `/proc/1/ns/net` and
  `/proc/1/ns/mnt`, affecting the fixture's simulated namespace check. No
  namespace/sandbox checks were bypassed or weakened.
- `uv run --no-sync ruff check xeonolab tests scripts`: passed (CI scope).
- `uv run --no-sync python scripts/export_openapi.py`: passed; no OpenAPI diff.
- `uv run --no-sync python scripts/smoke_services.py`: all 11 services passed.
- `uv run --no-sync python scripts/verify_failure_injection.py`: all passed.
- `npm test`: 160 passed. `npm run build`: passed (existing bundle-size warning).
- With `CHROME_BIN=/usr/bin/google-chrome-stable`, direct `node --test` runs for
  `browser-acceptance-capability-discovery`, `browser-acceptance-shell`,
  `browser-acceptance-session`, `browser-smoke` and
  `browser-acceptance-work-widget-links`: 5 tests passed; none skipped.
- `git diff --check`: passed. No production verification performed.
