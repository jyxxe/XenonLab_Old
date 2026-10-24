# #339 / #305: execution policy authority and constraint resolution

At protected base `529e335`, service declarations bind an execution-policy id
and SHA-256 byte digest, but the offline declaration validator deliberately does
not resolve a policy. ADR0010 requires reviewed core-owned policy, permission
floors, hard resource/network constraints and current-state checks before service
admission. The independent #335 signature component authenticates declaration
bytes; a valid signature cannot make the referenced policy current or enabled.

## Delivered component

`xeonolab/service_execution_policy.py` provides a versioned SQLite authority and
an exact-byte resolver. A trusted core caller explicitly selects its fixed DB
path and must explicitly initialize it. No module is imported by service startup,
no management HTTP route is added, no production policy or scope is provisioned,
and no existing caller starts using this resolver automatically.

The generated schema is `infra/plugins/service-execution-policy.schema.json`.
The checked-in example deliberately expired at Unix time 1; it demonstrates
shape and cannot provide active policy. Schema validity alone is not authority.

Each policy binds one exact plugin id and exact capability set. Capability
bindings include id, kind and **exact stable triplet version**, permission
minimum/allowed sets and timeout. There are 1–64 capabilities, no duplicates or
foreign namespace ids, and at most 32 bounded permission strings per list.
Permission floors must be subsets of ceilings. A manifest may neither omit a
minimum permission nor add one above its ceiling. Missing/extra capabilities,
kind/version substitutions and timeouts above the rule/global bound are denied.

The first profile accepts only the existing `xelibrary`, `embedding`, `llm`
and `lean` permission domains. It cannot request account, desktop, developer,
deploy, plugin-management or unknown-domain scopes. A permission listed here is
**a constraint on future authorized invocation**, not a grant, token or permission
to call the owning service. In particular, a publisher's requested data-write
scope cannot bypass owning-service ACL/revision/transaction rules. The future
gateway must intersect actual caller grants, declared permissions and policy and
must implement reviewed scoped exchange before any protected service call.
No caller token, cookie or provider credential is accepted or forwarded here.

Exact stable policy bindings deliberately avoid floating ranges and prerelease
admission in this initial profile. The shared capability-version contract is not
reimplemented or weakened; extending policy binding syntax is a reviewed future
change. Plugin release and source/artifact/signature provenance remain separate
immutable plan inputs.

## Required execution constraints

Every policy must explicitly supply all limits; there are no implicit production
allocations or permissive fallbacks. Protocol validation bounds are:

| Constraint | Accepted protocol range |
| --- | --- |
| memory | 16 MiB–64 GiB |
| CPU | 1–64000 millicores |
| PID count | 1–1024 |
| private scratch | 0–1 GiB |
| concurrency / queue | 1–128 / 0–1024 |
| input / output bytes | 1–16 MiB each |
| startup / invocation deadline | 1–300 seconds each |
| drain deadline | 0–300 seconds |

These maxima bound the format; they do not reserve capacity or authorize those
values on a particular host. A future protected inventory/admission coordinator
must apply tighter reviewed target and aggregate allocation ceilings and deny
when enforcement is unavailable.

The only initial isolation profile requires nonprivileged private namespaces,
read-only root filesystem, gateway-only ingress with no egress, no secrets and
no host mounts. The fixed profile means no publisher URL, image tag, command,
environment, secret reference, mount path or network exception is accepted.
Extending network/secret/persistent-storage profiles needs a reviewed contract
and enforcement, rather than accepting arbitrary strings.

This component **does not create or inspect a namespace/container**, configure
network rules, reserve memory/CPU/PIDs/storage, kill a workload or attest that
these requirements hold. The executor must prove actual controls before a
candidate can become routable; syntax/constraint tests are insufficient.

## Authority and publication

Policy input is strict bounded UTF-8 JSON, at most 64 KiB, with duplicate keys,
unknown fields, non-finite numbers, invalid types and excess nesting denied.
Parser and generated schema exclude whitespace in ids/versions/permissions.
Policies have explicit issued/expiry times and at most 24-hour validity.
Expired/not-yet-active policy, failed/invalid clock or corrupt/missing state
denies resolution. The operator must arrange reviewed renewal; expiry is not an
availability fallback that silently enables an old policy.

`ExecutionPolicyStore(..., management_enabled=False)` is the default.
`publish` and `disable` additionally resolve the current real identity and
require an interactive human and separate `plugin-service:manage` permission.
An Agent, API key (including one attributed to a human), desktop administrator
without the dedicated permission or deploy approver cannot manage policy.
This component does not provision that permission or implement MFA/fresh-login
ceremonies beyond the existing identity kind/API-key checks.

Mutations require an expected global revision and UUID operation id, use the
existing immediate SQLite transaction, and atomically update state, revision,
immutable publication history and actor/action/input audit. Concurrent writers
cannot commit from the same expected revision. A failed audit insert rolls back
publication or disable, with fixed diagnostics. Revisions cannot overflow a signed
63-bit integer.

Idempotency is keyed by actor/operation id and exact action/id/input digest and
expected revision. Reusing the id for another input conflicts. A retry returns
the current authority revision plus the historical applied revision; it does not
claim that the historical policy is currently enabled. Disabling preserves
policy history and commits denial. A later explicit human publication can restore
it under a new CAS revision; disable is **not** permanent artifact/key revocation,
which is a separate trust-authority gate.

Readers open existing databases in SQLite read-only mode and close connections
on success or failure. No absent database is created by resolution. A bounded
consistent snapshot verifies history/current identity and exact body digest.
Turning management off does not erase a persisted disable. Reinitialization
cannot recreate missing metadata over existing policy/current/audit history.

## Resolution and integration boundary

`resolve_execution_policy(declaration_bytes, manifest_bytes, store)` reuses the
service declaration validator and binds the referenced policy to its exact raw
stored bytes. It checks plugin/capability/permission/time budgets against a fresh
enabled policy and rereads current revision/expiry before returning immutable
constraints, digests, authority revision and validity ceiling. A concurrent
disable, profile replacement, expiry or any global revision change denies the
in-flight result rather than silently mixing generations.

The source-repository CLI is read-only:

```sh
uv run python scripts/resolve_service_policy.py declaration.json manifest.json \
  --policy-db /operator-selected/core-policy.sqlite --minimum-revision 1
```

It never initializes/publishes policy, fetches a URL/artifact, starts a process,
changes a registry, writes routes or issues credentials. Its JSON explicitly
includes `admission_granted: false`; errors are fixed and do not echo filenames,
DB details or publisher contents.

No result is a transferable admission receipt. A policy can be disabled after
the final read. A future coordinator must jointly fence current trust/signature,
policy, global dependency snapshot, account grants, capacity, health, workload
identity and approved deployment plan/generation at actual admission. Separate
authority receipts cannot provide a distributed atomic commit or justify cached
permission. Foreign dependency handling follows the declaration validator and
must still be resolved against one complete registry before admission.

The path/parent directory, SQLite files, clock and external revision checkpoint
must be provisioned and protected by the maintainer. This code does not prove OS
custody or resist a privileged attacker rolling back the whole DB and clock.
The optional minimum-revision floor needs a separately retained trusted value
to detect restore rollback. No production path/roots/grants are installed.

## Validation

The final standalone focus records 156 passes, including the execution policy,
existing declaration, trusted Tool adapter and Deploy Gateway tests. Complete
signature/policy/declaration/strict execution/version focus records 311 passes.
Two integration-only cases use real Ed25519 signatures and independent policy
and trust databases: a valid signature cannot satisfy replaced/disabled policy,
and valid policy cannot replace revoked signer evidence. No runtime admission
or true isolation is inferred from these independent negative gates.

The real Draft202012Validator checks the generated schema, expired example,
control/unknown-domain scopes and trailing newlines in all identifier/version
locations. This uses the prepared #333 dev environment; no JSON Schema runtime
dependency is added by this PR. Ruff and OpenAPI without drift pass. The first
full run was deliberately interrupted for the schema newline-consistency repair;
it is not terminal full-suite evidence. Final frozen full results follow below.

Production deployment, real identity/root provisioning, resource/network
isolation, actual containers, protected release/lease integration, service
routing, user grant exchange, provenance builds, recovery drills and visual
acceptance remain unimplemented or unverified. No merge, approval or release
is performed by this developer change.

## Frozen full results

On `529e335`, the standalone full suite records **908 passed / 13 failed**,
432.02 seconds. Failed identifiers exactly match the 13 reproduced baseline
Lean fixture failures; no new failure is present. This standalone branch does
not duplicate the separately submitted #337 test fix.

The complete #305 composition, including #337's identical fixture fix, records
**1225 passed**, exit 0, 492.23 seconds. This includes all pending trusted
adapters, lifecycle, strict execution registry, versions, signature authority,
this policy component and its two integration-only cross-authority cases.
The five policy source/test/schema/CLI/example files are byte-identical between
standalone and composition trees.

CI-pinned uv 0.12.1 accepts `--locked`; CI-scope Ruff and OpenAPI without drift,
all 11 service smokes and transaction/process/crash failure injection pass.
No dependency/lock change is introduced. This evidence does not imply protected
CI success or production/runtime acceptance.

The maintainer subsequently merged #323 (LLM Provider), advancing protected base
to `2c57f6f`. Publication synchronizes onto that baseline. The full composition
already includes the LLM adapter; rebase-specific focused results are recorded
separately rather than calling the earlier standalone full a new-base full run.

After the clean rebase onto `2c57f6f`, policy/declaration/Tool/deployment plus the
newly merged Chat Provider and native LLM regression focus records **195 passed**.
The five policy sources remain identical to the complete full-run composition.
Ruff and OpenAPI export pass without drift. The 908/13 standalone full result
above remains scoped to the preceding base; it is not a separate new-base full.
Complete full-run staged source tree: `81065c430d2341887c13a82e9d86d70840272a7c`.

## Final merged-signature-base regression

After #336 merged, this branch is synchronized onto `f139973`; ADR0010 retains
both authority components. The three signature/policy integration cases are now
committed normal CI tests because the signature implementation is in the base.
They cover policy replacement/disable despite a valid signature, revoked signer
despite valid execution policy, and digest-bound foreign dependencies remaining
unregistered and unresolved without network/process side effects.

Current-base focus: **312 passed**. Full unmodified branch: **1130 passed / the
same 13 baseline Lean fixture failures**, 434.81 seconds. The failed identifier
set comparison passes. The separate validation worktree changes only the exact
PR #338 test file; its full result is **1150 passed**, exit 0, 437.97 seconds.
Its staged source tree is `7d8736937d1985359cf54aa54da808fdb1694de2`.
That result is explicitly a fixture-only overlay, not an unmodified-head claim.

Policy module, CLI, original policy tests, schema and expired example are
byte-identical to the earlier `6eb699f` implementation. Ruff, locked dependency
consistency, repeated byte-identical OpenAPI export, all 11 service smokes and
transaction/process/crash fault injection pass. No runtime/root/grant, flag,
approval or production change was performed.

### Deployment guard baseline synchronization

Rebased onto maintainer merge `71a9c53` (#342), preserving both ADR sections.
The component implementation/tests/schema are byte-identical to `e4b9a41`.
Current-base focused tests: **326 passed**; Ruff, OpenAPI export with no diff,
and diff checks passed. Earlier full results retain their recorded baseline;
this documentation conflict rebase is not described as a new full-suite run.
