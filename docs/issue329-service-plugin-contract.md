# #329: Phase 3 security proposal and offline contract

Design: [ADR 0010](adr/0010-service-plugin-runtime-security.md). Status proposed;
maintainer review and actual runtime/security acceptance are outstanding. This
increment does not implement a service controller or change Deploy Gateway.
The offline validator is not imported by service startup or exposed via HTTP.

Artifacts:

- `xeonolab/service_plugin_contract.py`: strict bounded local declaration/manifest
  byte validation, existing registry checks, exact manifest hash and safe errors.
- `infra/plugins/service-plugin-declaration.schema.json`: generated declaration
  schema; test checks it stays equal to the model's schema.
- `infra/plugins/examples/service-{plugin-declaration,capabilities}.json`: synthetic
  data, zero artifact/commit/policy digests deliberately not resolvable trust.
- `scripts/validate_service_plugin.py`: explicit local files only, generic bounded
  diagnostics; output warns provenance/policy/admission/runtime are not verified.

Python 3.12.14 validation commands (shared prepared environment, no dependency
or lockfile changes):

```sh
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_service_plugin_contract.py tests/test_capabilities.py tests/test_deploy.py tests/test_executor.py tests/test_plugin_manifest.py -q
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync ruff check xeonolab tests scripts
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync python scripts/export_openapi.py
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync python scripts/validate_service_plugin.py infra/plugins/examples/service-plugin-declaration.json infra/plugins/examples/service-capabilities.json
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/test-tmp
git diff --check
```

77 focused tests pass, including legacy deploy/executor/registry tests, CLI safe
success/rejection and a byte-validator test that prohibits socket, HTTP and child
process calls and confirms the builtin registry is unchanged. CI-scope Ruff and
OpenAPI export pass (no API diff). The synthetic declaration also validates using
pending PR #321's stricter CapabilityRegistry loaded in an isolated local Python
process; this is compatibility evidence, not an integrated merge or admission.

No actual signature, secret-store access, OCI image fetch, workload/container,
network sandbox, delegated grant, production deployment, restore drill or visual
acceptance was performed. Those require the ADR's later implementation and real
acceptance matrix. Revert this commit to remove the offline contract; existing
APIs and installed plugins need no migration or reinstall.

Full suite: **783 passed / 13 failed in 273.74s**. The exact 13 failing names
match the previously reproduced unmodified baseline Lean failures. This desktop
account cannot stat `/proc/1/ns/net` and `/proc/1/ns/mnt` (PermissionError 13), so
sandbox preflight cannot initialize. No checks or sandbox protections were
changed, skipped or weakened. Protected CI and maintainer review are pending.
