# Issue #305 / #313: account-scoped capability resolution

The list endpoint already projects enabled plugin capabilities for the caller.
The detail endpoint now resolves from the same account projection, rather than
looking only in the builtin manifest. A caller can negotiate the version of an
installed plugin app or widget. Disabled, uninstalled, other-account and
permission-revoked plugin capabilities return `404 CAPABILITY_NOT_FOUND` before
version negotiation. Builtin capabilities lacking their required scopes also
return 404 on resolution; their descriptive list entries retain `enabled=false`.

This changes resolution metadata only. It adds no plugin execution endpoint,
new permission, database migration or service runtime. Existing app/widget
endpoints continue enforcing their own permissions and installation state;
iframe isolation and token boundaries are unchanged. Already loaded remote
iframes are not forcibly terminated by this backend change.

Regression coverage uses FastAPI TestClient and synthetic local plugin manifests:
app/widget install, account isolation, version success/conflict/invalid input,
disable/re-enable, live scope revocation, uninstall and denied builtin lookup.
These tests do not establish production or browser visual acceptance.

Rollback: revert this change; no stored data or installation snapshots change.
The previous detail endpoint then supports builtin metadata only again.

Remaining Phase 1 work: desktop discovery migration and its browser acceptance,
plus the migration example tracked by PR #317. Phase 2 and Phase 3 remain separate
milestones requiring adapter contracts and service security design respectively.

## Local validation

- Python 3.12.14 (bundled runtime), dependencies installed through a TLS-verified
  HTTPS mirror because the default PyPI connection failed with SSL EOF.
- `uv run --no-sync pytest tests/test_capabilities.py tests/test_plugins.py`:
  23 passed (also passed under the initial Python 3.14 environment).
- `uv run --no-sync ruff check xeonolab tests scripts`: passed (CI scope).
- `uv run --no-sync python scripts/export_openapi.py`: passed, no diff on Python 3.12.
- Initial full suite: 725 passed, 13 failed, all in `test_lean_verifier.py`.
  The unchanged c44076a baseline reproduces the same 13 failures. This desktop
  account cannot stat `/proc/1/ns/net` or `/proc/1/ns/mnt` (PermissionError 13),
  which also affects the Lean fixture's simulated namespace check. No sandbox
  checks were weakened. CI-style basetemp validation is recorded in the PR.
- Additional unscoped `ruff check`: 11 pre-existing errors under `infra/`;
  CI intentionally checks `xeonolab tests scripts`.
- No frontend, real plugin invocation, production or visual acceptance performed.
