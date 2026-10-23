# #305 / #313: isolate invalid installed UI-plugin snapshots

A temporary synthetic desktop database reproduces a core discovery failure on
base `43fee56`: changing one installed manifest's `permissions` to JSON integer
`1` causes capabilities, apps, and plugin-widgets GETs to return 500. No
production database or real account was modified.

All three projections now use the existing `PluginManifest` schema and require
its id to match the installation row. Invalid snapshots contribute no app or
widget. Healthy plugins, builtin capabilities and other accounts still work.
This also prevents malformed permission entries from being silently discarded
by capability discovery before authorization. Live permission checks remain.

Catalog metadata still comes from the published manifest. `installed`, `enabled`
and installed version remain stored account state; `installed_app` and
`installed_widget` are false when that snapshot cannot be validated. Merely
reading the catalog never replaces a snapshot with the latest published version.
An invalid installation can be disabled/uninstalled. Enabling fails with
409 `PLUGIN_SNAPSHOT_INVALID`; explicit reinstall checks permissions and accepts
the current catalog snapshot through the existing audited operation.

This isolates schema/identity-invalid installation records, not arbitrary
catalog/store failures or database tampering that produces another schema-valid
snapshot. Origin policy remains the existing publication/update validator.
No schema migration, downloaded code, remote registration or new execution path.
Rollback is a code revert; installation records are not rewritten by discovery.

Regression tests exercise actual authenticated APIs with invalid JSON, non-object
JSON, malformed permissions, identity mismatch, bad version and malformed app.
They cover core/healthy/other-account availability, absence from detail queries,
non-mutating catalog reads, disable, denied enable, explicit reinstall and uninstall.
The registry's older raw fixture is now a complete valid legacy PluginManifest
rather than a partial declaration with a dotted id the install API never accepts.

Validation (Python 3.12.14; shared prepared environment, no dependency changes):

```sh
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_plugin_snapshot_isolation.py tests/test_plugins.py tests/test_capabilities.py -q
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync ruff check xeonolab tests scripts
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync python scripts/export_openapi.py
../issue305/.venv312/bin/python scripts/smoke_services.py
../issue305/.venv312/bin/python scripts/verify_failure_injection.py
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/test-tmp
```

32 focused tests, CI-scope Ruff, all 11 service smoke checks and failure injection
pass. OpenAPI changes only document the new PATCH-install 409 response. No
frontend changes; no real visual acceptance or production validation performed.

Full suite: 734 passed / 13 failed in 273.40s. The exact 13 failing test names
match the previously reproduced unmodified baseline Lean failures: this desktop
account cannot stat `/proc/1/ns/net` and `/proc/1/ns/mnt` (PermissionError 13),
so the test sandbox preflight cannot initialize. No sandbox or checks were
weakened. Protected CI and maintainer acceptance remain outstanding.
