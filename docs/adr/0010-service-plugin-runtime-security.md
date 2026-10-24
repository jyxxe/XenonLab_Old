# ADR 0010 — Independent service plugins: admission and lifecycle

Status: **proposed**, #329 / #305 Phase 3. This is a security design for review,
not approval to execute a service. ADR 0009 continues to govern UI and trusted
in-process capabilities. No production service, endpoint, grant or executor is
added by this proposal. Runtime acceptance below remains outstanding.

## Evidence and scope

At `feature/platform-v1@43fee56`:

| Existing source | Reuse | Missing for service plugins |
| --- | --- | --- |
| `xeonolab/deploy.py`, `docs/api/deploy-gateway.openapi.json` | release approval, scope checks, singleton lease, holder/release/expiry/epoch checks, unresolved-deployment fence and audit | policy/provenance binding, service lifecycle and routing generation |
| `infra/systemd/executor.py` | exact configured commit/artifact match, fixed targets, SSH host verification, rollback on activation failure | plugin sandbox, resource/network policy, service attestation; it must not receive arbitrary manifest commands |
| `infra/devagent/deploy_runner.py` | protected broker invocation, exact-SHA preflight, immutable bundle hash and staged inventory | approved service-plan inventory and revocation checks |
| `xeonolab/common.py::token_identity` | signed issuer/audience/expiry/subject verification and scopes | plugin-specific audience, workload binding and delegated invocation grants |
| `xeonolab/capabilities.py` | capability ids, kinds, versions, namespaces and account visibility | authoritative service generations, live status and admission; `runtime=service` is descriptive only today |
| `xeonolab/plugins.py` | existing per-account UI install snapshots and audited operations | service deployment; UI installation must never trigger it |

Source inspection, not production configuration inspection. `/health/ready` only
shows service availability; it cannot establish sandbox, identity or provenance.
Pending Phase 2 adapter PRs provide process-local hooks, not a persistent global
lifecycle coordinator. Their adoption and CI are separate from this proposal.

Initial service runtime: independently isolated, digest-pinned OCI workloads
managed by a protected executor. Runtime backend/container manager is chosen by
maintainers during implementation; OCI format alone proves no isolation. No
arbitrary Python imports, shell command fields, publisher-selected deployment
hosts, runtime URLs or Docker socket access. Existing fixed platform inventory
and its deployment authority remain separate until reviewed support exists.

## Trust and immutable inputs

Untrusted publisher input is a small **declaration**, bound to the exact bytes
of the existing universal capability manifest. See
`infra/plugins/service-plugin-declaration.schema.json` and the two synthetic
`infra/plugins/examples/service-*.json` files. The example's zero artifact,
commit and policy digests are placeholders, not runnable or trusted artifacts.

`schema_version=1` binds plugin id/version, exact source commit, OCI artifact
digest, capability-manifest digest and execution-policy id/digest. Initial service
capabilities are tool/agent/provider/worker, `runtime=service`, and have a bounded
1–300 second invocation timeout. UI descriptors continue using existing APIs.
The declaration contains no repository URL, process arguments, endpoint URL,
environment values, file mounts, credentials, secret values or deploy scopes.

The offline validator checks bounded JSON, duplicate members, the strict
service declaration, exact manifest-byte digest, namespace/version/source,
capability kinds/tier/timeouts and the existing registry rules. It returns no
registered capability, trust decision or execution handle. It deliberately does
not fetch an artifact, verify a signature, resolve a policy or authorize scopes.
It does not decide external dependency compatibility: admission must resolve all
versions against one immutable registry snapshot and reject missing, disabled,
incompatible or cyclic dependencies across plugins. No resolving by network.

The trusted coordinator produces a separate **resolved deployment plan**. It
binds declaration/manifest/artifact byte hashes, verified publisher subject and
namespace ownership, provenance evidence hash, signer/key/trust-policy revision,
execution-policy bytes/hash, exact target inventory, release id/commit, resource
limits, network destinations, workload subject/audience, secret-reference
versions, migration/rollback evidence, capability and dependency versions, and
the expected old/new routing generations. A plan changes if any input changes.
The protected approval is over this full immutable plan digest, never just an
image tag, publisher name, declaration-valid boolean or mutable profile id.

Signatures must cover those immutable artifacts/provenance and bind publisher,
namespace and source commit to an authorized signer. Select a reviewed signature
format/verifier and root provisioning in a later implementation PR; do not
invent a new signing algorithm. Unknown signer, invalid signature, revoked key,
namespace mismatch, untrusted origin or unavailable revocation evidence denies
admission. Hashes alone are integrity references, not signatures or trust.
Maintainer-controlled trust roots must live outside publisher-writable manifests.

## Control plane and stored state

Proposed records (future additive migration, not created by this PR):

- Immutable declaration, profile and resolved-plan snapshots keyed by digest;
  provenance verification result tied to the exact trust-policy revision.
- Plugin deployment row: plugin/namespace, admitted plan, desired state,
  monotonic generation, deployment/release id, expected previous generation,
  observed state/health revision, policy/revocation revision and timestamps.
- Per-account capability grants, accepted plan/version and grant revision;
  service discovery is a projection of grants, not evidence of authorization.
- Durable transition/outbox and invocation admission records keyed by operation
  id, generation and caller; audited actor/action/reason/digests and result.

The core owns these records. Plugins cannot write them or query platform storage
directly. New service management permission (proposed `plugin-service:manage`)
must be separately provisioned through existing account authorization; neither
`desktop:admin`, a plugin's own manifest nor UI install grants it. Mutation needs
expected generation, idempotency id and applicable protected approval. Policy
chooses authorized administrators; actual activation/upgrade uses only the
maintainer's protected Deploy Gateway route. Ordinary developers prepare code,
tests and PRs and cannot approve, acquire deploy authority or activate workloads.

Desired state and observed state are distinct; a healthy heartbeat cannot enable
a disabled plugin. State changes use transactional compare-and-swap and a durable
outbox. Workers and receivers reject old generations/epochs and deduplicate the
same operation id. Reusing an operation id with different input is a conflict.
No second coordinator may proceed when a previous deployment is unresolved.

| Transition | Required evidence and behavior |
| --- | --- |
| declared → admitted | syntax, namespace ownership, signed provenance/revocation, profile ceilings, all dependencies and immutable resolved plan; no execution |
| admitted → staged | exact approved plan and release, valid maintainer lease/release/epoch, fixed target inventory; artifact fetch only through trusted allowlisted staging, hash verified before extraction/start |
| staged → running candidate | sandbox/identity/network/quota checks and startup deadline; candidate has no public invocation route or user grants |
| candidate → enabled generation | readiness identity matches plan, dependency/contract checks, no revocation, compare expected generation, atomically switch route snapshot and audit |
| enabled → disabled | commit denial/generation fence before acknowledgement, remove discovery, deny new calls, drain/cancel bounded admitted work then stop; this does not wait for plugin consent |
| enabled → unhealthy | gateway stops admission on expired/failed health, bounded backoff/breaker; core and unrelated routes stay ready |
| upgrade | new approved immutable plan and candidate; keep previous generation serving until eligible, then atomic route switch; no mixed dependency snapshot |
| rollback | disable affected generation, verify previous plan still trusted/compatible and data restoration proven; switch only to approved exact digest, not mutable `latest` |
| revoke | persist denial and increment revision, reject new calls and routes even if plugin reports healthy; cancel/revoke grants/secrets where supported; stop workloads via protected executor |
| uninstall | deny and drain first; remove runtime routes/identity/secrets, retain required audit and user data; destructive data purge is separate authorized action |

Account grant revocation stops only that account, platform disable stops all.
A request admitted before the disable transaction may drain within its original
deadline; requests admitted after it must fail. State acknowledgement waits for
route-generation fencing at admission authorities. Workers unable to confirm
current state fail closed. Cached enabled state alone is insufficient. Policy
sets a bounded watch/reconciliation interval and token lifetime, but these bounds
do not permit acknowledging disable while a stale gateway still admits calls.
Emergency network containment may block traffic immediately; it cannot install
or upgrade code and must produce durable audit when the control plane recovers.

Core database errors, restart, split brain, stale executor reply or partial
activation leave a non-routable/denied or explicitly unresolved state. Reconcile
against durable intent and authenticated receiver evidence. Never mark a timeout
completed or clear a deployment fence merely because a lease expired. Current
Deploy Gateway's checks are reusable but its executor does not presently attest
a service plan/generation: receiver-side verification is required before use.

## Identity, invocation and data ownership

The gateway validates the caller against current account scopes/grant revision,
plugin state/generation, compatible capability and dependencies on **every
admission**. Required permission floor comes from reviewed core policy and the
capability contract; the publisher cannot weaken it. Effective access is the
intersection of caller grants, registered capability permissions and execution
policy. All required scopes must be present in that intersection or the call is
denied; missing permissions cannot silently downgrade a capability. Discovery
visibility, possession of a workload token or being on the LAN
is not authorization. Unauthenticated/ungranted callers get existing 401/403;
absent/disabled capability returns 404, version conflict 409, admission capacity
429, unavailable 503, upstream safe failure 502 and timeout 504. Preserve existing
business endpoints' established error/ACL semantics during migration.

Each deployment generation gets its own workload identity and exact audience.
Authenticate gateway-to-service and service-to-core with short-lived,
cryptographically verified workload credentials (reviewed mTLS and/or audience
restricted OIDC). Admission must bind subject to plugin id, generation, plan
and allowed routes. The current generic platform audience is not sufficient.
No `deploy:*`, account-management or plugin-management grants for workloads.
Issuer selection and TLS name/root checks remain exact; no TLS bypass or fallback
to development tokens. Credential rotation/revocation is part of lifecycle.

Do not forward a user's bearer token, cookies or provider/admin credentials to a
plugin. A future reviewed gateway grant/exchange protocol must mint audience and
capability restricted, short-lived, non-transferable invocation credentials
bound to caller account, operation, generation, request id and input digest, with
replay/admission controls. Workload credentials alone cannot impersonate users.
Until this protocol is implemented and tested, service plugins cannot call
protected XeLibrary/account/other platform APIs. Plugins receive only authorized,
minimal copied input; ACL decisions, canonical source rows, transactions and
publication of derived data stay in the owning service. Recheck ACL/revisions
before committing a delayed result. A tool's output is untrusted data, not an
instruction granting authority. Side effects require existing owning-service
idempotency and audit rules; transport retries cannot replay writes blindly.

## Sandbox, quotas, network and secrets

A maintainer-owned execution policy supplies hard ceilings and exact allowed
resources. Publisher references request a policy; they do not create or broaden
one. Increasing permissions, destinations, secret references, mounts or quotas
creates a new plan and approval. Deny if the selected runtime cannot enforce it.

| Boundary | Required enforcement before candidate starts |
| --- | --- |
| host | dedicated unprivileged identity/user namespace or separately isolated VM; no privileged/container runtime socket, host PID/network, host root mounts, devices, extra capabilities or privilege escalation |
| filesystem | read-only verified image, bounded private scratch; only explicit per-plugin data volumes, no platform DB/credential/runtime-directory mounts; retention/purge policy separate |
| resources | hard memory/PID/CPU/storage/process limits, request/input/output byte ceilings, bounded concurrency/queue, startup/invocation/drain deadlines; actual kill/cleanup on deadline; no unbounded retries |
| network | default-deny ingress and egress; ingress from authenticated gateway only, egress exact reviewed service destinations/ports; DNS policy, redirect/rebinding protection and destination enforcement at transport boundary |
| protected networks | deny cloud metadata, loopback/link-local, host management, PVE, Forgejo/OIDC admin, Deploy Gateway and arbitrary LAN/Internet; approved internal data gateway routes use scoped identity, not broad subnet exceptions |
| secrets | mount only versioned policy-selected references through protected store, least privilege, no user token or deploy/admin/provider master key; no manifest/env/log plaintext; rotate on upgrade/revocation and remove on stop |

No arbitrary health URL, DNS target, download URL or host path from a declaration.
The resolved profile defines a fixed runtime endpoint, TLS identity and safe
health protocol. HTTP redirects are refused, DNS answers revalidated/pinned under
network policy, proxy environment inheritance disabled, responses byte-limited.
TLS and network-layer egress enforcement must agree; application allowlists alone
are insufficient. A service can crash or exhaust its own limits without crashing
the core. Cancellation must release gateway slots, child processes and streams.

Health is authenticated, bounded and bound to expected plugin/plan/generation;
liveness is not authorization. Separate startup/readiness/liveness deadlines,
consecutive-success/failure thresholds, health TTL and bounded retries are core
policy. Capability dependency failure removes only dependent service readiness,
not the platform's identity/audit/desktop health. Validate output schema and
capability version before publishing results. Redact upstream errors and secrets;
log only safe error class, request/operation id, plan digest and generation.
Metrics label by reviewed capability and bounded state, not caller input/token.

## Upgrade, rollback and supply-chain revocation

The deploy release's existing artifact digest must bind the full resolved plan
and workload artifact, not merely list an unrelated platform bundle. The broker,
gateway and fixed inventory receiver all verify the same approved digest. No
change to `Release` or executor protocol is made here; adding service-plan
binding is a prerequisite implementation, not something today's API guarantees.

Stage verified artifacts outside active routes; reject tar traversal/symlink
escapes and mutable image tag substitution in the eventual staging implementation.
Data/schema migrations declare owning service, compatibility window, backup and
restore evidence. No destructive migration before an approved recovery path.
Dual generations must not concurrently write incompatible data; quiesce/fence
writers or reject the upgrade. Automatic rollback is allowed only within the
approved plan's exact known-safe recovery action, lease/fence and trust policy.
Otherwise disable and leave unresolved for maintainer recovery. Revoked old
artifacts/keys cannot be reinstated merely to improve availability. Keep audit
and data intact for investigation; uninstall is not implicit data deletion.

## Acceptance and implementation sequence

The original #330 supplied the proposal and offline declaration contract.
The #335 signature increment adds a core public trust store and verifier.
The #339 increment adds a core-only versioned execution policy authority and
constraint resolver. Full plan/provenance admission, grant issuer, runtime
controller, routes, deployment and real sandbox acceptance remain unimplemented.
Local validation passing must never be a deployment check named
"service trusted" or turn an artifact into an enabled capability.

Implementation can proceed in reviewable increments, each with its own Issue/PR:

1. Protected, versioned policy/provenance store and resolved plan admission;
   namespace ownership and trust-root/revocation provisioning, deny by default.
2. Persistent lifecycle/outbox with compare-and-swap generations, account grants,
   audit, denial-before-ack and gateway discovery/invocation coherence.
3. Reviewed issuer/grant exchange protocol and gateway transport adapter; no
   remote execution until identity/network/quota requirements are enforced.
4. Protected Deploy Gateway/broker/inventory protocol binding and isolated runtime
   controller; signature verification and hard sandbox controls proven on a
   synthetic workload in a dedicated acceptance environment.
5. Upgrade/rollback/revocation and migration recovery drills; maintenance approval
   and production acceptance before any real service-plugin activation.

| Gate | Required evidence before runtime acceptance |
| --- | --- |
| declaration | bad/duplicate/oversize JSON, unknown fields/version/runtime, mutable tag/URL/command inputs, wrong exact manifest bytes, namespace/permission/dependency/time-budget errors rejected |
| provenance | substituted bytes/commit/namespace/key, bad/missing signature, revoked/unknown signer, stale trust roots and unavailable revocation all deny |
| policy | unowned namespace, scope-floor weakening, unapproved network/mount/secret/quota/profile change denied, policy mutation changes plan digest |
| identity | wrong issuer/audience/subject/plan/generation, expired/replayed grant, user-token forwarding, service direct access and management/deploy scope denied |
| deployment | absent approval, wrong plan/release/holder, expired lease/stale epoch, unresolved deployment and forged receiver confirmation cannot activate routes |
| isolation | real network denial including metadata/DNS rebinding/redirects, no host credentials/sockets, actual memory/CPU/PID/storage exhaustion and deadline kills contained; core remains responsive |
| lifecycle | concurrent enable/disable, grant revocation, stale replicas, controller crash/outbox replay, health spoof/expiry, partial route switch and lost replies preserve denial and audit; retries idempotent |
| compatibility | existing UI install/apps/widget behavior unchanged, capability scopes/versions preserved, source ACL checked before and after delayed work, no `.xl` change or unauthorized result publication |
| recovery | exact digest cutover, incompatible writer fence, safe approved rollback and tested restore; revoked prior version not restored, unresolved failure remains fenced |
| secrets and audit | no private values in declarations/errors/logs/metrics/test artifacts, bounded diagnostics, credentials rotate and grants expire/revoke |

Automated synthetic tests and actual sandbox/network/identity/restore evidence are
both required; mock success alone proves no isolation. Maintainer security review
must select/provision the concrete signature verifier, workload issuer, runtime
backend and policy ceilings before the affected runtime step. These operational
choices are prerequisites with fail-closed defaults, not excuses to grant more
rights to an ordinary development agent. Phase 3 remains incomplete until this
matrix has authoritative evidence and maintainer acceptance.

## Signature component increment (#335)

The [DSSE/Ed25519 component](../issue335-service-signatures.md) implements exact
service-declaration byte/type signatures, explicit public-key namespace ownership,
unique-key thresholds, current policy/expiry checks and permanent key/artifact
revocation. The selected format is the standard DSSE v1.0.2 JSON envelope and PAE;
cryptography supplies Ed25519. The core-only SQLite policy store has default-off
management, dedicated human permission checks, CAS/idempotency and atomic audit.
It is never selected or initialized by a plugin manifest or at API startup.
No production root or grant is installed, and no management HTTP route is added.

This profile/component is submitted for security review; it is not approval to
execute a plugin. It authenticates a signing claim rather than proving OCI
existence, source build provenance or execution-policy enforcement. A receipt
cannot authorize a runtime or replace current trust checks. Protected DB/clock
custody, external rollback checkpoint, root distribution and all deployment,
isolation, grant, routing and recovery gates above remain necessary.

## Legacy deployment protocol rejection (#341)

The current platform release protocol now rejects unsupported extra request,
stored manifest and executor job/manifest/artifact fields rather than silently
discarding purported service-plan/policy/generation constraints. See the
[protocol regression evidence](../issue341-deploy-protocol.md). Existing valid
platform releases retain their approval/lease/fixed inventory paths. This is a
fail-closed compatibility guard, not the future resolved-plan approval binding,
receiver generation attestation or permission to execute service workloads.

### Source-build provenance gate (#346)

`service_plugin_provenance.py` verifies a DSSE-signed in-toto/SLSA source-build
claim against explicit signer/builder/namespace/source/config rules in the
current core trust store. Its initial profile binds the requested OCI and exact
capability-manifest hashes plus source commit. Existing publisher keys have no
builder permission by default. Rules, current revocation and verification-time
checks are independent of the declaration signature; neither proof grants
admission or establishes a SLSA level. The producer profile and rollout limits
are documented in `service-source-build-v1.md` and `issue346-service-provenance.md`.

### Global dependency evidence (#343)

`capability_snapshot.py` constructs a bounded immutable graph from exact local
manifest bytes and caller-supplied disabled capability IDs. It rejects global
missing/incompatible dependencies, cycles and ambiguous namespaces, then resolves
an enabled dependency-first closure with a content digest. This does not prove
namespace ownership, current availability or any other authority; the caller must
bind verified sources and current revisions into the future approved plan. It
adds no registration or execution path. Limits, compatibility and evidence are
recorded in `docs/issue343-capability-dependency-snapshot.md`.

## Execution policy increment (#339)

The [execution policy authority](../issue339-service-execution-policy.md) binds
an exact policy byte digest to a plugin and exact capability id/kind/stable version
set. It enforces permission floors/ceilings and requested timeout constraints
against a durable, enabled, unexpired policy revision. Human-only explicit
management, CAS/idempotence and atomic audit provide reviewable publication and
disable operations. No HTTP management route, production policy or account
permission is installed; readers deny missing/disabled/expired state even with
management off.

The first profile requires nonprivileged private namespaces, read-only root,
gateway-only ingress, no egress, secrets or host mounts, plus explicit resource,
byte, concurrency/queue and deadline limits. These are required constraints,
**not evidence that an executor enforces them**. Only existing XeLibrary,
Embedding, LLM and Lean permission domains are accepted by this profile, with
exact stable capability versions. Broader permissions, prerelease policy bindings
and network/secret/storage profiles require reviewed extensions.

Policy resolution returns immutable constraints and digests, never an invocation
grant or enabled route. Current signature/provenance, all global dependencies,
actual resource allocation/isolation, user scopes/grants, service identity, health,
protected approved plan/lease and final generation/revision fences must all be
verified by the future coordinator. File/clock custody and an external anti-rollback
checkpoint remain operational requirements, not properties proven by local SQLite.

