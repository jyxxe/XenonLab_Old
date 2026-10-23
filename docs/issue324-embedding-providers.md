# #305 Phase 2 / #324: trusted Embedding Provider interface

## Interface and compatibility

`EmbeddingProvider` owns model description, local worker state, request preparation
and start/close. `PreparedEmbedding` snapshots a worker and returns a normalized
`EmbeddingResult`. Default `D100MProvider` and `QwenProvider` delegate to the
existing implementations; no weights, model defaults or vector spaces change.
The HTTP kernel retains input/image validation, scopes, caller-scoped queue
bookkeeping, metrics and response schemas. It no longer chooses concrete encoder
methods or assembles provider-specific model-space response metadata.

`ProviderBinding` maps public aliases, capability id/runtime, metric/history labels
and image support to trusted code. Startup-time replacement is demonstrated
without changing public callers. Existing `fast` / `D100M-Embedding` and `quality`
/ `Qwen/Qwen3-VL-Embedding-2B` aliases remain; replacement implementations may
supply their own descriptor, revision and labels via a binding. This does not
implement or activate the real EmbeddingGemma migration in #312.

Default compatibility retained:

- discovery requires `embedding:read`; encoding requires `embedding:write`,
  including write-only principals with no read scope;
- unconfigured fast requests do not create queue entries; quality failures are
  recorded after entering the existing queue bookkeeping;
- queue/history remain principal-scoped, including counts and pagination;
- default history labels remain `d100m` / `quality`, and metric labels remain
  `d100m` / `qwen3vl2b`;
- fast response metadata keeps its original omissions; quality returns the same
  revision/dimensions/preprocessing/normalization identity;
- fast input/token limits and busy lock remain in D100M; quality purpose,
  truncation, model-space checks, image bounds, busy lock, process deadline and
  process termination remain in the existing Qwen worker;
- images retain the original `reject` truncation policy; errors for unsupported
  image models retain quality-slot accounting;
- unexpected encoder failures remain HTTP 500 `INTERNAL_ERROR`, are counted as
  failures and never include exception details in the public message.

Result normalization rejects mismatched batch/dimension counts, empty/non-finite
vectors, invalid usage/revision and responses that fail the existing response
schema before recording a successful request.

## Operation-scoped capability gates

The existing provider capability ids describe model discovery; new `-encode`
capabilities carry the write permission. This avoids requiring read scope for an
existing write-only client.

| Slot | Describe capability | Encode capability | Runtime |
| --- | --- | --- | --- |
| fast | builtin.platform.provider.d100m | builtin.platform.provider.d100m-encode | api |
| quality | builtin.platform.provider.qwen-worker | builtin.platform.provider.qwen-worker-encode | service |

Every operation resolves the current validated manifest, kind/runtime, minimum
and declared scopes, version and process-local enabled state. Missing/disabled
capabilities return 404; incompatible versions 409; scope failures 403; invalid
bindings 503. Composite dependencies are rejected until trusted composite
provider bindings exist. Unsupported explicit encode `timeout_seconds` declarations
are rejected rather than silently ignored or advertised as enforced.

D100M is trusted synchronous CPU code and has no new hard execution deadline.
Python thread cancellation is not a CPU isolation boundary. Qwen retains its
real process-backed configured deadline, including stopping the worker on timeout;
that behavior is tested through the public encoding API. Hard CPU/resource limits
for new independent service plugins remain Phase 3 work.

## Lifecycle and fault isolation

Provider replacement is allowed before service lifespan startup. Replacing a live
worker requires a service restart, avoiding unbounded retired workers and closing
workers still used by a prepared request. Start/close ownership stays in the
registry; Qwen closes the exact worker it started. Startup failures quarantine
only that binding, display `unavailable` and reject its encoding while other
providers continue. Close failures are logged with the slot only and do not
prevent other owned workers from closing. Malformed/failed descriptors become a
per-provider unavailable descriptor; unexpected result/encoder failures are safe
500 errors and failed queue entries.

The kernel-only `set_enabled(slot, bool)` hook hides disabled discovery and rejects
new encoding. It does not unload weights or forcibly interrupt an active call.
Local physical worker state remains observable: a disabled loaded model can still
show configured/loaded, while readiness and invocation are denied. Queue state is
honest when the primary provider is absent. Flags and bindings are process-local,
reset with a new service instance, and are not a persistent account install or
cross-service lifecycle system. Aggregate desktop builtin metadata does not
synchronize these local flags; this remains #305 follow-up work.

## Rollout and rollback

`EMBEDDING_CAPABILITY_ADAPTERS=1` (default) enables manifest/enablement checks.
Trusted configuration `0` bypasses those gates and retains the default provider
protocol, lifecycle/result validation and endpoint scopes. Invalid flag values
fail startup. A code revert restores the previous direct calls. No database
migration, new caller scope or production configuration change is introduced.
UI plugin manifests cannot import/register these implementations; there is no
remote backend registration API or arbitrary executable loading.

The Docker recipe includes the required public builtin manifest. Staging its
actual COPY layout and importing from an isolated Python process resolves both
encode capabilities. This checks resource availability, not an image build or
container runtime. Docker CLI is absent (exit 127). The current manifest also
passes the stricter validator in PR #321; this is not a full merged deployment.

## Evidence limits

Tests use real FastAPI routing, synthetic providers/keys/encoders and an actual
Qwen child process with a synthetic encoder. The public-route timeout test checks
worker termination and failed queue accounting. They do not validate real model
weights, retrieval quality, performance, production, or the EmbeddingGemma model.
No frontend or visual change/acceptance is included. #305 remains open.


## Local validation

Python 3.12.14, repository dependency versions, no lockfile changes:

- `uv run --no-sync pytest tests/test_embedding_providers.py tests/test_embedding_queue.py tests/test_qwen_worker.py tests/test_metrics.py tests/test_services.py`: 61 passed.
- `mkdir -p runtime` then `uv run --no-sync pytest --basetemp runtime/test-tmp`:
  742 passed, 13 failed in 268.53s. The exact failure set matches the unchanged
  baseline's 13 Lean verifier tests; this account gets PermissionError 13 reading
  `/proc/1/ns/net` and `/proc/1/ns/mnt`. No Lean code/test or sandbox gate changed.
- `uv run --no-sync ruff check xeonolab tests scripts`: passed (CI scope).
- `uv run --no-sync python scripts/export_openapi.py`: passed; only Embedding's
  common 404/409 capability error responses are added to the generated contract.
- `uv run --no-sync python scripts/smoke_services.py`: all 11 services passed.
- `uv run --no-sync python scripts/verify_failure_injection.py`: all passed.
- Actual Docker COPY layout staged into a temporary filesystem: isolated Python
  resolves both encode capabilities. No Docker build/container run performed.
- PR #321's stricter validator accepts the new manifest and separate read/write
  binding resolution. This does not prove a full merged deployment.
- `git diff --check`: passed. No real-model, frontend, visual or production
  acceptance performed; all model vectors in new tests are explicitly synthetic.


## Baseline refresh after desktop discovery (#319)

Rebased onto `feature/platform-v1@acc52f3`. Every current builtin UI entry
is retained exactly, as is every previous backend entry; the capability id set
is the exact union without duplicates. Backend provider/chunker source files
are byte-identical to the pre-refresh backup; this fixes the manifest conflict
without changing their implementations. Only this Agent's feature branch is
updated, never the protected base.

Current validation: **86 focused tests pass; 747 full-suite tests
pass / 13 baseline Lean failures in 430.07s**. The exact failed-name set
matches the previously reproduced unmodified baseline (`/proc/1/ns/net`, `mnt`
stat PermissionError 13). No sandbox protections were changed or skipped.
CI-scope Ruff, OpenAPI export (no extra generated diff) and diff check pass.
Earlier counts above refer to earlier snapshots and selected test sets.

```sh
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_embedding_providers.py tests/test_quality_provider.py tests/test_embedding_queue.py tests/test_capabilities.py tests/test_plugin_manifest.py -q
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/rebase-test-tmp
```

No real model/production/visual acceptance, deployment, approval or merge was
performed. Current protected CI and maintainer acceptance remain outstanding.

## Authority availability interoperability (#331 / #332)

EmbeddingProviders now checks the core Identity's `require_capability` for the
binding's provider root before local/manifest gates and before preparing a call.
This check also runs when `EMBEDDING_CAPABILITY_ADAPTERS=0`; local enable cannot
override authority denial. Strict lifecycle mode requires a verified snapshot
revision and callable core hook, otherwise 503. Default mode retains old-core
compatibility until rollout. Actorless readiness remains physical/configuration
health, not authorization. Model responses retain typed response validation and
now set no-store without bypassing the response model.

The #332 extension controls D100M/Qwen as availability groups: each root governs
read descriptors and encode admission. Encode scopes are still checked against
the distinct `-encode` write capability, so a write-only caller does NOT need
`embedding:read`. No remote registration, credential or data grant is created.
The core's parent mapping hides/refuses encode metadata while the root is stopped;
no independent child mutation is exposed. Native endpoints have equivalent gates
before workers and queue records; image files are closed on refusal, with no
claim that FastAPI multipart parsing itself is avoided.

100 focused Provider/quality/worker/queue/capability tests pass; core focused
suite: 97 pass. Combined source passes 155 focused tests and four embedding
lifecycle cases with the adapter flag off. These cover two instances with
separate queue files and one live synthetic authority, aliases/text/image routes,
write-without-read permission, descriptor/encode parent filtering, restart,
re-enable, missing authority, rejection without jobs or encoding and upload
cleanup. Combined source construction and local tree are recorded in
`docs/issue331-capability-lifecycle.md`. Full results below refer to these
updated sources; historical counts above retain their earlier scopes/snapshots.

All consumers must upgrade the snapshot vocabulary before new group mutations;
query-only or query/LLM parsers fail closed on new disabled ids. Actual deployment,
flags and grants are unchanged. No real model/GPU, OIDC, production or visual
acceptance was performed; existing D100M CPU and Qwen process-boundary limitations
remain. This is admission control, not runtime termination or Phase 3 execution.


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


Rebased onto maintainer base `ef4996b` after #330 merged. The ADR conflict was
resolved by retaining both the Phase 3 proposal link and this feature section;
own implementation files were compared byte-for-byte to the prior PR head and
are unchanged. Targeted feature/registry/discovery/snapshot/service-contract
regression: **159 passed**. CI-scope Ruff, regenerated OpenAPI without
working-tree drift and diff checks pass. This rebase did not repeat each old
full suite; the complete 1035/13 composition including PR #334 is separate
evidence and its same 13 Lean baseline failures remain disclosed. No production
flag, permission, merge or deployment changed.
