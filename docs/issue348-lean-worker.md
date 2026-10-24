# #348 / #305: Lean worker resolution and live job authorization

Current baseline: `feature/platform-v1` at `a80c051` (maintainer merged #338).
Implementation began at `71a9c53`; the rebase adds only the approved fixture
and its documentation. Own runtime/new-test/OpenAPI bytes remain identical. The builtin registry already
advertised `builtin.platform.worker.lean-verifier`, but synchronous requests and
queued execution called the runner directly. Queued jobs did not reauthorize
and restart resumed durable rows using only their stored owner. Four baseline
counterexamples are recorded: missing capability still accepted sync/queue,
revoked queued owner still ran, and concurrent requests exceeded queue capacity.

## Trusted capability binding

`LeanWorkers` binds one statically supplied synchronous worker to the existing
worker/service capability. It never imports manifest code, selects a publisher
URL/command or grants a new scope. Resolution checks presence, kind/tier, stable
`^1.0.0` version, `lean:verify` and every declared permission, timeout budget and
trusted executable dependencies. The initial binding has no dependency adapters:
a nonempty dependency graph is rejected, not silently invoked or fetched.

The builtin manifest now explicitly sets the existing native maximum timeout
of 120 seconds. This preserves the existing 60-second default and 1–120-second
HTTP input range when enforcing metadata budgets. A requested timeout above a
configured capability budget is rejected, not silently enlarged or truncated.
Missing/disabled capability is 404, incompatible version 409, unsupported budget
422 and invalid/unavailable contract 503. Existing scope/network/queue/sandbox
checks remain; OpenAPI describes these outcomes without changing request fields.

`create_app(..., runner=...)` remains a trusted platform-code replacement seam,
with the continuation protocol `runner(..., register=None, *, check_active)`.
Async/incompatible callables are rejected at construction. A replacement is
trusted code reviewed with the worker contract, **not untrusted plugin code**
executing in the API process. It must cooperate with checkpoints and preserve
resource/process supervision. The native worker implements these guarantees;
there is no general hard cancellation of arbitrary in-process Python callables.

Results have bounded stdout/stderr, supported status/exit-code semantics and
honest `dependency_lock_checked: false`. Unknown result fields and false lock
claims are rejected. Axiom annotations are recomputed by the kernel using the
existing source/output extractor, rather than accepted from a replacement.
This still reports observed axioms, not a new soundness proof or dependency-lock
verification. No fake compiler, fabricated embedding or production toolchain is
installed by this change.

## Authorization and continuation

The kernel retains a minimal in-memory request containing only the app and
Authorization header. It is never written to the job DB, source/result files,
logs or child arguments/environment. The worker receives only a kernel-owned
continuation callback; no raw bearer value. Static trusted Python code is not a
separate confidentiality boundary. Untrusted Lean child processes receive the
existing stripped environment, not the request or callback.

HTTP admission still pays the normal inbound rate limit. The shared internal
`refresh_identity` helper reuses the existing issuer/signature/account authority
checks without charging a second inbound request; remote authority 429 remains
a rejection. This matches the helper already proposed in #331 instead of adding
a second authentication implementation. Each job pins its subject/kind and
requires the current `lean:verify` scope and all worker permissions.

A queued or synchronous job forces fresh authorization **after acquiring the
shared execution slot**, before calling its worker, and again before accepting
a result. Native execution checks the continuation every 250 ms; effective
account/lifecycle refresh is cached only within the existing <=5-second
continuation interval. This is not a cache between HTTP requests. An authority
call can take its existing bounded timeout; authority failure or rate limiting
never becomes a fallback grant. Production needs a live account authority or a
trusted core resolver; a valid standalone JWT is insufficient. Development
fixtures may use explicitly configured mutable development identities.

Lifecycle snapshots remain strict by default for existing index checkpoints.
Lean's pre-rollout identity-only mode does not waive current permissions, and is
not a production JWT-only fallback. Once a verified lifecycle protocol is seen,
missing/invalid metadata on any refresh fails instead of downgrading. The core's
current lifecycle control vocabulary does **not** yet expose a global Lean
management knob; no such endpoint is claimed or added here. Expand it only after
these actual invocation gates are available and all consumers are upgraded.

`set_enabled(bool)` is a trusted process-local hook with no HTTP endpoint. Each
state change advances a local generation pinned by admitted jobs. Disable then
re-enable cannot resurrect old queued/running work or accept its stale result.
New requests can be accepted after re-enable. This counter is not a durable
cross-service routing generation. Readiness checks worker/configuration/sandbox;
it is not evidence of production authority reachability or complete isolation.

## Native process and resource ownership

The original root-owned/non-writable file checks, SHA-256 toolchain pins,
network/mount namespace detector, stripped child environment, session/process
group, CPU/address-space/file/process rlimits and output readers remain owned by
the kernel. Capability metadata cannot bypass them. The existing process-group
kill path is used on cancellation, shutdown, revoked/failed continuation and
native timeout. Local native tests observe a child before revocation and prove
it has been killed/reaped before a result is accepted.

An independent watchdog enforces the native child walltime even if an
in-process authorization callback is blocked. The API response may wait for the
bounded authority operation/cleanup; this does not extend child execution.
Failure to start a watchdog or output reader kills/reaps the child before an
error is reported. These are native worker guarantees, not certification of
third-party execution or process-tree/cgroup isolation for arbitrary workloads.

Sync and queued jobs share the original bounded execution pool. Queue capacity
is now checked and inserted in one SQLite write transaction; the earlier count
is only a preflight. A barrier test makes two requests observe zero queued rows:
the baseline accepted both at capacity 1, the new code accepts one and returns
429 to the other. Failed source writes release the slot and remove the request's
private directory. Admission failure creates no executable job or result.

## Recovery, privacy and compatibility

Only live in-memory checkpoints can authorize queued execution. Startup marks
legacy queued/running rows `failed` with `LEAN_REAUTHORIZATION_REQUIRED` (or
`cancelled` for an already requested cancellation), clears any nonterminal result,
and **never automatically reruns them using their stored owner**. Terminal
history, source files, ownership and existing retention remain. A fresh
HTTP submission with a valid current identity creates a new job. This necessary
security change replaces automatic restart replay and is intentionally tested.

Shutdown stops admission, kills registered native process groups, cancels queued
futures, drains workers and drops in-memory credential contexts. Unstarted work
keeps non-executable history. Status/cancel remain owner-and-scope protected and
available when the worker is locally disabled; they do not start compilation.
Revocation still denies these endpoints through their existing scope check.
There is no DB schema migration, `.xl`, account preference or plugin install change.

Deploy the worker and its manifest together. Reverting the bundle must not reset
failed interrupted rows to queued, restore revoked permissions or synthesize a
credential from an owner. Quiesce/drain or interrupt work through the new safe
shutdown path first; prefer reverting only a faulty trusted implementation while
retaining the authorization gates. No production flag/grant or unit is changed.

## Production transport and remaining acceptance

ADR 0003 requires the fixed TLS account authority. The existing Lean unit has
`PrivateNetwork=yes` and `RestrictAddressFamilies=AF_UNIX`, so it cannot directly
make an ordinary HTTPS authority request. This PR refuses insecure production
authority URLs or production execution with no live source; it does not relax
network isolation to make a test pass. A configured but unreachable authority
fails closed on the request. The production transport/job-grant choice is raised
in #345: keep sensitive authentication in the kernel/gateway and use a reviewed
protected IPC/job authorization flow to the isolated worker, or another reviewed
compatible authority transport. This is still a maintainer deployment decision.

Tests use local synthetic identities, approved fake toolchain files and exactly
simulated namespace metadata. They do not prove a real Lean installation,
production namespace/VM/OCI isolation, authorization reachability or visual
acceptance. No production inventory, keys, approval, lease, deployment, merge or
Issue acceptance is performed by this developer branch. Complete Phase 3
admission, workload identities/grants and real isolation/recovery remain open.

## Validation

Four baseline red cases and final commands/results are recorded below. Coverage
includes static worker replacement, contracts, live permissions/subject changes,
local stop/re-enable generations, queue/restart, result publication, strict
lifecycle metadata, authority failure/429, immutable native limits, independent
walltime during blocked authorization, resource-start/storage failures, atomic
capacity, synthetic bearer non-persistence and unchanged sandbox rejection.

### Final local evidence

Current functional commit: `837e5f0` (runtime/new tests/OpenAPI byte-identical to
initial `4342d5f`). CI uv 0.12.1, Python 3.12.14, fresh `uv sync --locked` environment;
no dependency or lockfile changes.

- Current `a80c051` original branch: `uv run --no-sync pytest -q --basetemp
  runtime/pytest348-a80-raw`: **1151 passed**, exit 0, 293.64 seconds.
- Earlier `71a9c53` original run: **1131 passed / 13 failed**, exit 1,
  293.95 seconds; failed names exactly equal the recorded Lean fixture baseline.
  This is historical evidence. The maintainer's #338 merge resolves that fixture;
  it is not silently overlaid on the original branch's current result.
- Complete local #305 combination: **1438 passed**, exit 0, 345.47 seconds,
  `uv run --no-sync pytest -q --basetemp runtime/pytest348-integration-final`.
  Commit `1cc669b1ee4ed9fdfa10d644440b69f80c764e01`, source tree
  `42ace4fedf32b19a484f3b726c107c92b82c42d2`.
- Own locked registry/version/auth/rate-limit/worker focused set: **142 passed**,
  including **55** new Lean worker cases. Complete combination Lean/lifecycle/
  index/provider/cross-evidence focused set: **143 passed**.
- Baseline counterexamples: missing registry capability sync+queue and revoked
  queued owner **3 failures before fix**; real barrier concurrency at capacity 1
  **1 failure before fix** (both accepted), passing with atomic insertion.

The complete combination extends the previous #305 tree `38b51028` (publisher
signature, provenance, policy, dependency graph, durable lifecycle and deploy
guard) with this worker. All new component non-doc files are exact; shared
`common.py` deliberately retains #332's full lifecycle-aware implementation,
whose `identity`/`refresh_identity` AST matches the helper extracted here.
The original Lean test module preserves #338's fixture/tests exactly except
for this PR's restart assertion, checked by AST comparison. Its final content
matches the approved fixture plus our restart change exactly on `a80c051`.
Both lifecycle and worker ADR sections are retained. This is a local validation
tree, not a protected-branch merge or a substitute for the original run above.

Own and combination Ruff pass. OpenAPI exact structural comparison shows only
Lean verify error response descriptions changed; request/other schemas are
unchanged. Repeated exports have stable byte hashes. Complete-combination
`scripts/smoke_services.py` passes all 11 services; transaction/SIGKILL/XeLibrary
recovery/claim-fence/webhook-dedup fault scenarios pass. `git diff --check` passes.
No skip/xfail or weakening of namespace enforcement was introduced.

Frontend code has no Lean API caller and was not changed; npm/browser gates
were not rerun locally for this backend library. Protected remote backend/frontend
CI remains a separate requirement. Actual Lean/production/visual/isolated-runtime
acceptance and the #345 transport/deployment decision remain unperformed.
