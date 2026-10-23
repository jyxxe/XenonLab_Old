# #331 / #305: durable authority controls for the query tool

This increment makes **one already implemented capability** use durable state:
`builtin.platform.tool.xelibrary-query`. The core store/protocol is reusable,
but no other capability is advertised as controllable until its invocation and
discovery paths are integrated. Providers, worker jobs, UI installs and service
runtimes retain their existing lifecycle. No code loading, deployment, approval,
service grant exchange, runtime stop or artifact upgrade is added here.

## Authority and admission

The desktop/account authority adds three tables to its existing private
`accounts.sqlite`: `capability_lifecycle_meta`, `capability_lifecycle` and
`capability_lifecycle_audit`. An absent control row means enabled at generation 0.
Every accepted distinct operation advances the global revision and that
capability's generation, including a requested state already in effect. A
transaction using the existing `BEGIN IMMEDIATE` helper commits state, revision
and audit together. Existing account tables/scopes and UI plugin snapshots are
not rewritten. Operation records are retained; future retention must preserve
idempotency tombstones and required audit rather than allow key reuse.

Management requires all of: an authenticated `human` OIDC identity, explicit
`capabilities:manage`, a non-API-key credential and enabled rollout configuration.
`desktop:admin`, `accounts:manage`, ordinary Codex/DevAgent identities and scoped
API keys alone cannot operate these controls. The scope is added to the account
scope vocabulary only; no OIDC role mapping, existing account grants or runtime
credentials are expanded automatically. Authorized administrators must provision
it explicitly using the established reviewed identity/account process.

New endpoints:

- `GET /api/v1/capabilities/{id}/lifecycle`: current enabled state and generation.
- `PATCH /api/v1/capabilities/{id}/lifecycle`: strict JSON with `enabled` boolean,
  `expected_generation` integer and UUID `operation_id`. Stale generation is 409
  `LIFECYCLE_GENERATION_CONFLICT`. Same actor/id replay with identical body is
  idempotent; changed body/id target is 409 `LIFECYCLE_OPERATION_CONFLICT`.
- A mutation response contains current state, `duplicate` and
  `applied_generation`. On a retry after a later mutation, current state is
  returned without reverting it; `applied_generation` identifies the historical
  operation. The original operation is not re-applied.

Unsupported ids (including core settings and unintegrated providers) return 404
`CAPABILITY_NOT_CONTROLLABLE`, before any control-state mutation. Invalid JSON
fields/types/generation/UUID return 422. SQL/audit failure rolls back and returns
503, never a successful acknowledgement or an enabled fallback. Persisted invalid
state makes authority requests unavailable rather than silently enabling it.

`GET /api/v1/auth/authorization` adds an optional protocol field:

```json
{"capability_lifecycle":{"version":1,"revision":0,"disabled":[]}}
```

It is read from one SQLite read transaction, not OIDC bearer claims, user input,
an adapter or a plugin manifest. The snapshot carries no credentials, publisher
URLs or executable data. The existing TLS-validated, proxy-independent
`AUTHORIZATION_URL` channel forwards the existing caller identity to the trusted
account authority. There is no new remote URL, shared-filesystem requirement,
service token or secret. Each request reads live state without an allow cache.
Known version, bounded integer revision and unique supported disabled ids are
validated; malformed state is rejected. Updated clients honor a supplied valid
snapshot even when the rollout flag is off. The authority always reads persisted
controls; turning its mutation flag off does not re-enable a stopped capability.

Capability list/detail and tool list/query invocation consult the same identity
snapshot. Disabled query is omitted from both lists, detail/query returns 404,
and its handler does not forward upstream. Other tools and builtin capabilities
are unaffected. Normal scope validation is retained before invocation; lifecycle
enabled state never grants a scope. Existing user Authorization/request-id
forwarding and source API authorization remain unchanged. State/discovery/tools
and authorization responses use `Cache-Control: no-store`.

Admission is the authority snapshot read for that request. An operation admitted
before disable commits may finish under the existing handler's transport bounds;
this increment does not kill/drain an already running process, revoke existing
streams or promise a new hard CPU deadline. A request admitted after the commit
sees denial. New requests cannot use a locally cached earlier enabled revision.
The global control applies to every account, while account grants still apply
independently. Only the core authority owns and mutates this state.

## Explicit deployment gate and compatibility

`CAPABILITY_LIFECYCLE_ENABLED` accepts only `0` (default) or `1`, captured at app
startup. On the authority, `1` permits the protected management endpoints. On a
consumer, `1` requires the live authority and a valid lifecycle snapshot: missing
URL, missing/old snapshot or unavailable authority yields 503; there is no local
JWT fallback. The existing authority 401/403/429 behaviors remain. With `0`, an
old authority without this new field is compatible until rollout; if a field is
supplied it is still validated and enforced. Local standalone development without
an authority remains the old behavior only while strict mode is off.

This flag is a deployment gate, not proof that all old clients have upgraded.
**Do not enable production mutations until every invocation instance is updated,
uses the same authority and has passed strict-mode acceptance.** An old binary
that ignores snapshots, a gateway deliberately configured outside that authority,
or a direct owning-service API is not disabled by this feature. The last is
intentional: disabling a Tool Gateway capability does not revoke the user's
underlying XeLibrary query permission. Disable the account/grant if that is the
intended restriction. No HTTP endpoint can upgrade an old consumer magically.

Maintainer-only staged rollout (not performed by this PR):

1. Deploy the updated authority with flag `0`; verify its new snapshot and empty
   control state. The three table additions are additive and default enabled.
2. Deploy **every** tool-gateway instance with this admission guard and flag `1`,
   preserving exact TLS/issuer/account authority configuration. Verify valid
   snapshot admission and denial on missing/unavailable authority; old versions
   must be removed from routing. No developer credential authorizes this step.
3. Explicitly provision the narrow human management grant, then enable the
   authority flag `1` through the protected maintenance/configuration flow.
4. With synthetic acceptance data, exercise disable/discovery/direct-invocation,
   multiple instances/restarts, account scope denial and re-enable, and retain
   real deployment/gateway evidence before declaring production acceptance.

If authority replicas are later added they must share one strongly consistent
control authority; per-host copied SQLite files are not a replicated authority.
This implementation has one authority and no watch cache, outbox executor or
asynchronous reconciliation. It does not claim Phase 3 service lifecycle.

Pending PR #321 refactors the same tool routes and metadata. Integration must
preserve `actor.require_capability` for query before invoking its adapter and
omit it from `ToolAdapters.describe`; its process-local enablement must intersect
with this authority state. Until that combination is tested, neither PR is
claimed integrated. Other pending Provider/Worker hooks need equivalent explicit
integration before expanding `CONTROLLABLE`. The live builtin manifest remains
the same; this PR does not duplicate their adapters.

Rollback: first stop management mutations and keep strict consumers/authority
available. Do not downgrade to binaries that ignore persisted denial while a
capability is disabled. An authorized operator must explicitly re-enable it (new
generation/audit) or keep the affected gateways out of routing before a code
rollback. Turning the flag to `0` on updated code preserves supplied/persisted
state. Keep the three tables/audit on rollback; deleting them is not a recovery
procedure. No `.xl`, plugin reinstall or data migration is needed.

## Validation

Python 3.12.14, prepared shared environment; no dependency/lockfile changes:

```sh
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_capability_lifecycle.py tests/test_accounts.py tests/test_capabilities.py tests/test_services.py -q
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync ruff check xeonolab tests scripts
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync python scripts/export_openapi.py
../issue305/.venv312/bin/python scripts/smoke_services.py
../issue305/.venv312/bin/python scripts/verify_failure_injection.py
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/test-tmp
git diff --check
```

62 focused tests pass: two gateway app instances sharing the live synthetic
account authority, persisted app reconstruction, query forwarding/denial and
metadata consistency, both accounts, scope revocation, strict/unavailable/missing
snapshots, CAS/idempotent retries, concurrent SQLite writers, audit-failure
rollback, dedicated human/no-API-key management and HTTP cache denial. CI-scope
Ruff, all 11 actual localhost service smoke processes and crash failure injection
pass. OpenAPI export adds the two lifecycle operations and request model only.

No production state/flag/grant was changed. No real OIDC/browser/visual acceptance
or multi-host rollout was performed; the authority transport in integration tests
is synthetic. Legacy smoke services run with the default flag, not a proof of
strict-mode production rollout. Full-suite and protected CI results are recorded
below; maintainer acceptance remains required.

An additional isolated Python OS process read the same committed disabled state
from a temporary synthetic SQLite database (empty environment, no network,
10-second process deadline). This verifies persistence beyond app-object reuse;
it is not multi-host or production authority/gateway acceptance.

Full suite: **752 passed / 13 failed in 284.12s**. The exact failed test names
match the previously reproduced unmodified baseline Lean failures: this desktop
account cannot stat `/proc/1/ns/net` and `/proc/1/ns/mnt` (PermissionError 13), so
sandbox preflight cannot initialize. No sandbox checks were altered or skipped.
Protected CI and maintainer acceptance are still outstanding.
