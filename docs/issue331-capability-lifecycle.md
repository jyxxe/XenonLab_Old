# #331 / #305: durable authority controls for query, LLM and embedding

This increment controls four existing builtin groups: the query tool, LLM
Gateway, D100M provider and Qwen provider. Query/LLM retain their specific
admission paths. Each embedding provider root is an availability gate shared by
its model discovery and text/image encoding paths, including the existing native
implementation. Its encode capability keeps the separate write authorization;
provider availability never grants or requires the read scope for encoding.
Other providers, workers, UI installs and independent services remain outside
this control set. No dynamic code, deployment or runtime termination is added.

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

Capability list/detail and the controlled public handlers consult the same
identity snapshot. Disabled query is omitted from tool discovery and returns
404 before forwarding. Disabled LLM is absent from capability discovery;
`/v1/models` and both JSON/SSE `/v1/chat/completions` return 404 without reading
the chat body, consuming a slot or preparing/calling a provider. Other tools and
capabilities remain unaffected. Embedding model catalogues omit a stopped provider;
all its aliases and encoding endpoints deny before queue insertion or encoding. Normal scope validation is retained before invocation; lifecycle
enabled state never grants a scope. Existing user Authorization/request-id
forwarding and source API authorization remain unchanged. State/discovery/tools/model-catalogue
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
**Do not enable production mutations until every authorization consumer is
updated to this snapshot vocabulary and every controlled invocation instance uses
the same authority with strict-mode acceptance.** Older query-only or query/LLM snapshot
parsers reject newly disabled embedding ids with 503 rather than ignoring them. Update
all authority consumers, not just the embedding gateways, before allowing new group mutations;
otherwise even unrelated authenticated requests on old strict consumers can
become unavailable. The protocol shape stays version 1; the supported controlled
id vocabulary is deployment-coupled and fails closed on unknown ids. An old binary
that ignores snapshots, a gateway deliberately configured outside that authority,
or a direct owning-service API is not disabled by this feature. The last is
intentional: disabling a Tool Gateway capability does not revoke the user's
underlying XeLibrary query permission. Disable the account/grant if that is the
intended restriction. No HTTP endpoint can upgrade an old consumer magically.

Maintainer-only staged rollout (not performed by this PR):

1. Deploy the updated authority with flag `0`; verify its new snapshot and empty
   control state. The three table additions are additive and default enabled.
2. Deploy **every** authority consumer and query/LLM/embedding gateway with this protocol and flag `1`,
   preserving exact TLS/issuer/account authority configuration. Verify valid
   snapshot admission and denial on missing/unavailable authority; old versions
   must be removed from routing. No developer credential authorizes this step.
3. Explicitly provision the narrow human management grant, then enable the
   authority flag `1` through the protected maintenance/configuration flow.
4. With synthetic acceptance data, exercise disable/discovery/direct-invocation,
   multiple instances/restarts, LLM JSON/SSE and both Provider flag paths, scope denial and re-enable, and retain
   real deployment/gateway evidence before declaring production acceptance.

If authority replicas are later added they must share one strongly consistent
control authority; per-host copied SQLite files are not a replicated authority.
This implementation has one authority and no watch cache, outbox executor or
asynchronous reconciliation. It does not claim Phase 3 service lifecycle.

Pending PR #323 supplies a trusted ChatProvider interface. Its authority check
must run before its adapter/legacy flag branch and before chat body/slot admission;
local enable cannot override global disable. Its actorless readiness remains a
configuration/health check, never authorization. This PR's native LLM guards and
matching descriptor make its standalone behavior enforceable before #323 lands.
Both branches copy the builtin manifest into the Docker layout. All gates still
control Gateway APIs only, not an external provider's credentials or service.

Pending PR #321 refactors the same tool routes and metadata. Integration must
preserve `actor.require_capability` for query before invoking its adapter and
omit it from `ToolAdapters.describe`; its process-local enablement must intersect
with this authority state. The tested #321 combinations are recorded in its document; this does not imply
maintainer acceptance or deployment. Other pending Provider/Worker hooks need equivalent explicit
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

## LLM authority increment (#322 / #323)

The shared authority additionally accepts `builtin.platform.provider.llm-chat`.
The new fixture exercises actual authority SQLite operations and two gateway app
instances with a synthetic HTTP provider. It covers model/capability discovery,
JSON and SSE success/denial, restart and re-enable, independent query state,
missing snapshot/outage/scope denial, no body/slot work on rejection, and an
already admitted SSE draining/closing while the next request is denied.

The direct-ASGI tests now pass a real synthetic `Identity` with `llm:use`, rather
than an empty `SimpleNamespace`. Two exploratory runs waited indefinitely for an
upstream-start signal after the empty actor failed the new guard; those identified
runs were terminated, corrected and are not counted as validation.

79 focused tests pass on the standalone core branch; Provider branch focused
suite: 55 pass. The combined tree's 110 focused tests pass, as do its four LLM
lifecycle cases with `LLM_CAPABILITY_ADAPTERS=0`. The imported authority fixture
is explicitly re-exported for pytest/Ruff; its four cases were rerun after that
lint-only correction. Both isolated branches pass Ruff and OpenAPI export with
no new API schema drift. `/v1/models` responses use no-store.

The combined source starts from `acc52f3`, adds #323 with the authority guard,
then #332's core changes. It retains the core's native query-tool block and
#323's Provider LLM block; the LLM descriptor and direct-ASGI fixtures are
identical, not duplicated. Local staged source tree:
`e6a3003c26dab92e7d38ddd84f74dbd3026082ca` (not a merge/release/signature).
The public models result shape and alias remain unchanged. Actorless readiness
can stay healthy during policy denial; it does not assert account authorization.

Docker COPY layout probes for both branches resolve the LLM capability in an
isolated Python process. Docker CLI is unavailable (`docker --version`: exit127),
so no image build or container-isolation acceptance was done. No real provider,
OIDC, multi-host rollout, production or visual acceptance was performed. Actual
control mutations/flags/grants were changed only in temporary synthetic tests.


Current full-suite results (Python 3.12.14, base `acc52f3`):

- Provider branch: **751 passed / 13 failed in 412.09s**.
- Core lifecycle branch: **759 passed / 13 failed in 428.55s**.
- Combined source: **781 passed / 13 failed in 290.73s**.

All three exact failure-name sets match the previously reproduced unmodified
baseline's 13 Lean verifier failures (`/proc/1/ns/net`, `mnt` stat PermissionError
13). No sandbox checks were skipped or weakened. The first two suites ran
concurrently; durations are not a performance comparison. The small subsequent
fixture re-export correction changed no test behavior; its four cases were
rerun. Source branches pass Ruff, export OpenAPI without new API-schema drift
and diff check. Combined 11-service smoke and crash failure injection pass.
Current protected CI and maintainer acceptance remain outstanding.

```sh
# Provider branch:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_chat_provider.py tests/test_llm_gateway.py tests/test_capabilities.py tests/test_plugin_manifest.py -q
# Core branch:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_llm_lifecycle.py tests/test_capability_lifecycle.py tests/test_llm_gateway.py tests/test_accounts.py tests/test_capabilities.py -q
# Combined source:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_llm_lifecycle.py tests/test_chat_provider.py tests/test_capability_lifecycle.py tests/test_llm_gateway.py tests/test_accounts.py tests/test_capabilities.py tests/test_plugin_manifest.py tests/test_services.py -q
LLM_CAPABILITY_ADAPTERS=0 UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_llm_lifecycle.py -q
# Each source in its own worktree/temporary directory:
mkdir -p runtime
# In the Provider worktree:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/llm-authority-test-tmp
# In the core worktree:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/llm-lifecycle-test-tmp
# In the combined worktree:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/llm-combined-test-tmp
```

No real provider/production configuration, grant or data was used or changed.

## Embedding provider availability groups (#324 / #325)

Two additional controllable roots are `builtin.platform.provider.d100m` and
`builtin.platform.provider.qwen-worker`. They are provider-wide availability
controls, not new data permissions. A stopped root denies model discovery and
all its encode operations, regardless of whether callers have read, write or
both scopes. Encoding still requires only `embedding:write` at the endpoint and
its existing write-capability floor; no read permission is added. The roots'
`-encode` children are hidden/rejected in unified capability discovery/detail
when their parent stops. Children are deliberately not independently mutable
control ids, preventing a misleading half-stopped provider state.

Both native and Provider paths enforce the root before queue insertion and
worker/encoder calls. Image handling also rejects before reading the parsed
UploadFile and still closes it. FastAPI parses JSON/multipart before entering
these typed endpoints; this is NOT a pre-network/pre-parser upload rejection.
The existing file/byte/model-space validation remains intact for admitted calls.
A stopped model returns 404, not a success with synthetic output. The other
provider and caller-scoped history/physical worker status remain available.
Health/configuration and process metrics are not account admission decisions.
D100M's synchronous CPU cannot be forcibly cancelled by this control; Qwen keeps
its existing process deadline/termination. Already admitted work may finish.

Protocol shape remains v1, but its supported-id vocabulary now includes all four
roots. Update every authorization consumer before enabling these mutations;
older parsers fail closed on new ids, potentially affecting unrelated protected
requests. This is a rollout requirement, not something a feature flag verifies.
All mutation flags/grants remain unchanged in real environments.

Core focused suite: 97 pass. Provider focused suite: 100 pass. The combined tree
passes 155 focused tests and four embedding lifecycle cases with
`EMBEDDING_CAPABILITY_ADAPTERS=0`. Tests cover two gateway instances with separate
local queue databases, alias/route coverage, read/write separation, actual image
success/rejection/closure, durable stop/re-enable, scope/authority failures,
no rejected job records/worker calls and parent/encode metadata filtering.
All workers/vectors/images are explicitly synthetic; no model download or real
embedding-quality claim. Earlier fixture mistakes were corrected before the
successful runs; unsuccessful exploratory outputs are not acceptance.

Combined source: base `acc52f3` plus current #325 Provider implementation and
#332 core controls. Keep #325's `xeonolab/embedding.py` (its registry applies the
same root gate) and use all core query/LLM guards. Manifest entries are explicitly
unioned without differing duplicates; the generated embedding OpenAPI conflict
is resolved by exporting the actual combined app, retaining its 404/409 contract.
A separate check requires all four roots plus both encode children to exist,
so deny-path tests cannot accidentally pass with missing declarations. Local
staged tree `cf7d9d8664b60b6cca7fd316ce680edcee88184d` is a test tree, not a release.

No real OIDC, GPU/model, multi-host, visual, production or service-runtime
acceptance was performed. The rollout, audit/CAS, failure and rollback semantics
above apply unchanged to these groups.


Current embedding authority validation (Python 3.12.14, base `acc52f3`):

- Provider: **750 passed / 13 failed in 419.47s**.
- Core: **763 passed / 13 failed in 434.46s**.
- Combined: **784 passed / 13 failed in 297.73s**.

All three exact failed-name sets match the original unmodified baseline's 13
Lean failures (`/proc/1/ns/net`, `mnt` stat PermissionError 13); no checks or
sandbox protections were weakened/skipped. The first two ran concurrently with
separate temporary directories; elapsed time is not a performance comparison.
Ruff and diff checks pass. OpenAPI changes on the native core add only the
embedding 404 admission responses; the combined Provider retains its existing
404/409 error contract. Combined actual 11-service smoke and crash failure
injection pass. Isolated staged Docker COPY layouts resolve both embedding roots;
Docker CLI is unavailable (exit127), so no image was built.

```sh
# Provider worktree focused and full:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_embedding_providers.py tests/test_quality_provider.py tests/test_qwen_worker.py tests/test_embedding_queue.py tests/test_capabilities.py -q
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/embedding-authority-test-tmp
# Core worktree focused and full:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_embedding_lifecycle.py tests/test_capability_lifecycle.py tests/test_llm_lifecycle.py tests/test_embedding_queue.py tests/test_quality_provider.py tests/test_capabilities.py -q
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/embedding-lifecycle-test-tmp
# Combined worktree focused, fallback mode and full:
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_embedding_lifecycle.py tests/test_embedding_providers.py tests/test_capability_lifecycle.py tests/test_llm_lifecycle.py tests/test_embedding_queue.py tests/test_quality_provider.py tests/test_qwen_worker.py tests/test_capabilities.py tests/test_accounts.py -q
EMBEDDING_CAPABILITY_ADAPTERS=0 UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_embedding_lifecycle.py -q
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/embedding-combined-test-tmp
```

The successful core focused run is 97 cases, including a corrected worker-like
image fixture with the existing default truncation argument and a closable
read-tripwire. The old unsupported-control negative test now uses the still
unintegrated index worker and independent encode child, because D100M is now
intentionally supported. Earlier unsuccessful exploratory runs are not counted.
No real weights, GPU, OIDC, model quality, multi-host, production or visual
acceptance was performed; protected CI and maintainer acceptance remain pending.
