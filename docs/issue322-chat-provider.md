# #305 Phase 2 / #322: trusted LLM Provider boundary

## Interface and ownership

`ChatProvider` supplies local configuration readiness, public model descriptors
and a `PreparedChat` for a canonical chat request. A prepared call supplies a
stream client/request or an async JSON completion. Implementations must normalize
their transport to the existing OpenAI JSON/SSE contract. They receive the parsed
body, not the incoming Request, platform bearer or identity object.

`HTTPChatProvider` retains the existing environment-configured implementation:
`LLM_BASE_URL`, `LLM_PROVIDER_MODEL`, `LLM_API_KEY_FILE`/`LLM_API_KEY` and the
optional TLS maximum remain server settings. File credentials retain precedence,
and rotation is read on the next prepare. Each prepared call snapshots its
backend URL, model and provider authorization header for the request. Its repr
omits fields; public model discovery still exposes `deepseek-flash` without the
provider key or internal model configuration. TLS certificate/hostname validation
remains enabled and httpx uses `trust_env=False` as before.

The gateway owns OIDC/scopes, inbound parsing/byte limits, stream admission,
absolute deadlines, SSE validation, response byte limits, disconnect cancellation
and shielded resource cleanup. Provider replacement does not change these
controls. The original user bearer is not forwarded to the provider; only the
server provider credential is added by the default prepared call. Provider
response headers are not copied to the public response.

## Capability resolution

A static `ChatProviderRegistry` resolves
`builtin.platform.provider.llm-chat@^1.0.0`. It checks kind `provider`, runtime
`api`, the trusted minimum `llm:use`, all additional declared scopes, version
compatibility and enabled state for every discovery/invocation. Unknown or
stopped capabilities return 404; scope failures remain 403, incompatible versions
409, kind/tier/permission/budget mismatches 503. Composite provider dependencies
fail closed until trusted dependency bindings are implemented. There is no
manifest import path, dynamic code loading or registration/lifecycle HTTP API.

Trusted kernel code may call `replace(provider)` or `set_enabled(bool)`. Each
already-prepared request keeps its provider snapshot and deadline; new requests
resolve the current binding. The hooks are process-local and reset on restart.
They are not a persistent, per-account backend plugin installation system.
Disabling hides LLM discovery at `/v1/models` and refuses new chat requests;
the desktop's aggregate builtin manifest metadata does not synchronize this
process-local flag. Cross-service lifecycle coordination remains follow-up work
under #305. Readiness checks local validated configuration, not real upstream
availability or model quality.

## Timeouts and errors

The gateway's existing `LLM_STREAM_TIMEOUT_SECONDS` remains the absolute budget
from request arrival. An optional capability `timeout_seconds` can shorten it,
including time already spent reading the body. The default manifest does not
shorten the existing configured budget. SSE keeps its original HTTP timeouts,
capacity slots, byte limit, safe error events and cleanup ownership.

JSON completion now also uses the same end-to-end absolute deadline, including
client cleanup, and maps timeout to 504 `PROVIDER_TIMEOUT`. HTTP/protocol/adapter
failures become safe 502 `PROVIDER_ERROR` responses. JSON serialization failures
are covered as provider protocol failures. As before, JSON releases the stream
admission slot before awaiting completion; this is stream capacity, not a new
JSON concurrency limit.

Provider preparation may report unsupported requests (422) or missing
configuration (503), but messages/details are replaced with canonical public
text. Other provider exceptions do not expose their details. Unexpected SSE
adapter failures after response headers emit the existing safe error event,
never `[DONE]`, and close resources. Existing preheader HTTP errors remain 502;
request-body deadline failure remains 408. Async deadlines are cooperative I/O
controls, not CPU/process isolation. All implementations remain trusted static
platform code; untrusted/service execution still requires Phase 3 security.

## Rollout and rollback

`LLM_CAPABILITY_ADAPTERS=1` (default) selects the registry binding. Trusted process
configuration `0` uses the original environment HTTP provider directly, bypassing
registry enablement/version/selection; invalid values fail startup. The prepared
call transport and JSON deadline/error fixes remain in both flag modes. A full
code revert rolls those back too. No data migration, new caller scope or provider
credential is introduced, and no production configuration was modified.

The Docker recipe now includes the public builtin capability manifest required
by registry resolution. A staged filesystem from its actual COPY directives
successfully resolves the LLM capability in an isolated Python process. This is
not a Docker image build or container execution; Docker CLI is absent on this
machine (`docker: command not found`, exit 127).

## Evidence scope

Tests use synthetic configuration/keys, httpx fake transports, real FastAPI
routing and direct ASGI disconnect exercises. Provider replacement is exercised
through the same public JSON/SSE callers. Existing direct-ASGI fixtures now use
a real synthetic `Identity` with `llm:use`, so new per-call permission checks are
exercised rather than bypassed by empty namespace actors.

Coverage includes replacement, enable/disable, discovery, readiness, scope/
version/kind/runtime checks, safe errors, JSON deadline/cancellation/recovery,
SSE deadline/unexpected failure, retained disconnect/cleanup/capacity regressions,
TLS validation, credential rotation and caller/provider credential separation.
No real provider endpoint, real key, production deployment or visual acceptance
was used. Embedding Provider migration and #312 model replacement are not part
of this change; #305 remains open.

## Local validation

Python 3.12.14, repository dependency versions, no lockfile changes:

- `uv run --no-sync pytest tests/test_chat_provider.py tests/test_llm_gateway.py tests/test_capabilities.py tests/test_services.py`: 48 passed.
- `mkdir -p runtime` then `uv run --no-sync pytest --basetemp runtime/test-tmp`:
  742 passed, 13 failed in 268.52s. The failure-name set exactly matches the
  unchanged baseline's 13 Lean verifier failures. This account receives
  PermissionError 13 statting `/proc/1/ns/net` and `/proc/1/ns/mnt`; Lean source and
  tests are unchanged. No sandbox checks were weakened or skipped.
- `uv run --no-sync ruff check xeonolab tests scripts`: passed (CI scope).
- `uv run --no-sync python scripts/export_openapi.py`: passed; only the LLM Gateway 502/504 descriptions change to cover JSON completion errors/deadlines.
- `uv run --no-sync python scripts/smoke_services.py`: all 11 services passed.
- `uv run --no-sync python scripts/verify_failure_injection.py`: all passed.
- Staged Docker COPY layout: isolated Python resolved the LLM capability and
  public model descriptor. Image build/container execution were not performed.
- The LLM manifest/binding also resolves with PR #321's stricter manifest
  validator. This checks validator compatibility, not a full merged deployment.
- `git diff --check`: passed. No frontend, visual or production test performed.


## Baseline refresh after desktop discovery (#319)

Rebased onto `feature/platform-v1@acc52f3`. The manifest conflict was resolved
field by field: every current builtin UI entry is unchanged, every prior backend
entry is unchanged, and the id set is exactly the union, without duplicates.
No provider/tool business implementation was changed by this refresh. Local
pre-refresh backup refs were retained; only the Agent's own feature branch will
be updated, never the protected base.

Current validation: **51 focused tests pass; 747 full-suite tests pass /
13 baseline Lean failures in 415.64s**. Exact failed names match the prior
unmodified baseline (`/proc/1/ns/net`, `mnt` stat PermissionError 13). No checks
were skipped or weakened. CI-scope Ruff, OpenAPI export (no extra generated
diff) and `git diff --check` pass. Full suite command:

```sh
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/rebase-test-tmp
```

Earlier validation counts above refer to their earlier source snapshots. This
refresh is not production or visual acceptance, deployment, approval or merge.

Exact focused command for this refresh:

```sh
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_chat_provider.py tests/test_llm_gateway.py tests/test_capabilities.py tests/test_plugin_manifest.py -q
```

## Persistent authority interoperability (#331 / #332)

ChatProviderRegistry now intersects local enablement with the core Identity's
`require_capability` hook. `CAPABILITY_LIFECYCLE_ENABLED=1` requires that hook and
a verified nonnegative snapshot revision, otherwise 503. Default mode remains
compatible with old Identity objects until rollout; a supplied hook is always
honored. This branch does not itself create an authority or management grant.

The service checks authority before the Provider/legacy flag branch and before
reading a chat body or taking a capacity slot. `LLM_CAPABILITY_ADAPTERS=0` and
local `set_enabled(True)` cannot override a global stop. `/v1/models`, JSON and
SSE refuse a stopped capability with 404. Scope checks, alias/credential mapping,
TLS, deadlines and resource cleanup remain unchanged. Healthy model responses
use no-store; #332's core applies it to denied discovery responses too.

Actorless readiness deliberately does not authorize any account; it checks local
binding/configuration. A stream already admitted from a valid snapshot may drain
within its existing budget after disable. New requests fetch the live authority
and are denied; this is not remote process termination or service deployment.

The #332 extension checks both native LLM endpoints before the adapter interface
is installed and adds the matching builtin descriptor. It exposes no additional
controls for other providers/workers. All authority consumers must be upgraded
before LLM mutations: query-only parsers reject the new disabled id, rather than
silently ignoring it. Rollout remains default off, with the dedicated human
management grant and strict account-authority configuration; no real flags or
grants were modified here.

Validation: 55 Provider/LLM/capability/schema focused tests pass on this branch;
79 authority/LLM/account/capability focused tests pass on standalone #332.
Combined source passes 110 focused tests and four LLM lifecycle tests with
`LLM_CAPABILITY_ADAPTERS=0`. These exercise actual synthetic authority mutations,
metadata/JSON/SSE boundaries, two gateway instances/restart, strict snapshot
failure, scope denial, pre-body/pre-slot rejection, local enable intersection,
and admitted-stream cleanup followed by new-call denial. Ruff/OpenAPI export
pass, with no new response schema changes. Exact combined source construction
and tree are recorded in `docs/issue331-capability-lifecycle.md`.

No real provider, OIDC, production, multi-host, visual or service runtime
acceptance was performed. COPY-layout probes confirm manifest presence only;
Docker CLI still exits 127, so no image was built. Full-suite results below refer
to these updated sources; earlier counts above retain their source snapshots.


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


Rebased onto maintainer base `ef4996b` after #330 merged. The ADR conflict was
resolved by retaining both the Phase 3 proposal link and this feature section;
own implementation files were compared byte-for-byte to the prior PR head and
are unchanged. Targeted feature/registry/discovery/snapshot/service-contract
regression: **118 passed**. CI-scope Ruff, regenerated OpenAPI without
working-tree drift and diff checks pass. This rebase did not repeat each old
full suite; the complete 1035/13 composition including PR #334 is separate
evidence and its same 13 Lean baseline failures remain disclosed. No production
flag, permission, merge or deployment changed.
