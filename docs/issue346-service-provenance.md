# #346 / #305: source-build provenance verification

Baseline: `feature/platform-v1` at `71a9c53` (maintainer merged #342).
The implementation began at `f139973`; its own runtime/test files remain exact
through the ADR-only baseline reconciliation. The existing declaration verifier
checks a publisher's signed claim. It does not establish who built the OCI
artifact from which source. This PR implements the separate source-build gate
using real DSSE/Ed25519 and current core-owned trust policy, with no new database,
management endpoint, automatic registration, builder provisioning or execution.

`verify_service_provenance(envelope, declaration, manifest, trust_store)` verifies
in-toto Statement/v1 + SLSA provenance/v1 using the initial
[OCI source-build profile](service-source-build-v1.md). It binds both requested
OCI and exact manifest-byte subject hashes, source commit, repository, recipe,
build type, builder and eligible public signers. A declaration's own publisher
signature remains independently required by future admission. Neither proof
alone authorizes the execution policy, user, workload, route, release or lease.

The shared `TrustPolicyStore` now accepts an optional `provenance_builders` list
on each public key. The default is empty: old publisher keys cannot create build
proofs implicitly. Each rule has `builder_id`, `repository`, `build_config` and
`plugin_ids`; namespaces must be a subset of that key's already authorized
namespaces. Rules are deeply frozen in a verified snapshot, bounded to 8 per key,
with bounded HTTPS identities and repository-relative config paths. No URL or
config is fetched, resolved or executed. Duplicate rules and namespace expansion
are rejected. The public schema is regenerated; semantic URI/path/subset checks
also run in Python and are not fully represented by JSON Schema.

Publication retains authenticated human + `plugin-service:manage`, API-key deny,
explicit default-off management, CAS, UUID idempotence, audit and atomic writes.
The verifier re-reads current policy revision/digest/time after crypto and claim
checks. Its threshold counts unique current keys authorized for **this** builder,
source/config and namespace. Old evidence cannot bypass expiry, policy changes,
removed builder authorization or permanent key/artifact revocation. Removing a
builder rule can be reversed only by an explicit new human policy publication;
permanent key/artifact tombstones retain their stronger no-restoration semantics.

Limits: existing 64 KiB DSSE envelope, 16 signatures, 32 KiB provenance payload,
16 subjects, 64 materials; existing 64 KiB/24-hour trust-policy bounds remain.
Duplicate JSON members, non-finite numbers, invalid UTF-8/JSON, malformed or
unexpected claims and ambiguous required subjects fail with fixed safe errors.
Standard extension fields outside externalParameters are ignored; the build
interface is checked exactly. PAE authenticates the actual accepted media type,
and both standard in-toto JSON envelope types are supported. Base64 and crypto
logic are shared with the existing declaration verifier.

The common JSON loader previously passed raw bytes to `json.loads`, which
silently autodetects UTF-16/32 even without a BOM. A baseline reproduction
accepted a UTF-16LE declaration. The loader now decodes UTF-8 first and parses
text, consistently enforcing the intended byte contract. Standard UTF-8 JSON without a BOM retains its behavior; autodetected encodings
and BOM-prefixed documents are now rejected consistently. Existing dependency/timeout/namespace and signature checks remain.

Read-only command:

```sh
uv run python scripts/verify_service_provenance.py proof.json declaration.json manifest.json \
  --trust-db /operator-selected/core-trust.sqlite --minimum-revision 1
```

The tool never initializes a missing DB or publishes policy; it prints evidence
hashes and `admission_granted: false`. A caller supplies a core-owned path; no
publisher chooses it. The synthetic test fixture uses ephemeral keys and clocks,
not production roots. There is no credential, signing-key generation CLI,
network fetch, arbitrary runtime command or policy rollout here.

## Compatibility, rollout and remaining acceptance

Existing policies without the optional field retain identical declaration
verification. Old strict verifiers reject policies containing this new field;
upgrade **all policy consumers before** an operator publishes builder rules.
The PR does not publish them. Rolling back requires first restoring an old-shape
policy through the current human/CAS interface, preserving all permanent
revocations, then reverting code. Do not discard the DB or revocation history.
No migration of existing policy rows is required.

Evidence proves a claim from an explicitly trusted signer/builder under a current
policy. It does not prove that artifact bytes were fetched or exist, that a builder
is honest/isolated, or any SLSA level. The eventual protected executor must verify
actual artifact bytes against the approved plan digest. Full multi-authority
admission/plan, runtime identity, user grants, protected receiver binding and real
sandbox/network/quota/recovery acceptance remain separate. Runtime-backend/target
choice is raised for maintainers in #345; production and visual acceptance were
not performed. No production flags, public trust roots or grants are changed.

## Validation

Tests use real Ed25519 signatures and current public policies for digest, source,
namespace, purpose, builder, parameter, threshold, revocation and time races;
standard extensions and media types; exact-byte substitution; strict UTF-8;
human/CAS/idempotence/audit; bounded inputs and unavailable authority; readonly
CLI and blocked network/process operations. A manifest-substitution counterexample
first failed, then passed when the profile required both subject digests.
Final commands and full regression evidence follow below.

### Final regression evidence

Current functional commit: `11481e0` (own code/tests/schema/CLI byte-identical to
initial `52950bb`). Python 3.12.14, CI-pinned uv 0.12.1, fresh environment prepared
with `uv sync --locked`; dependency files unchanged. With that locked environment:

- Current `71a9c53` base, original branch: `uv run --no-sync pytest -q
  --basetemp runtime/pytest346-71a9-raw`: **1150 passed / 13 failed**, exit 1,
  366.15 seconds. All 13 failed names equal the recorded baseline Lean fixture
  failures exactly. No new exclusions, skip/xfail or sandbox weakening.
- Earlier `f139973` original run completed, not interrupted: **1119 passed /
  the same 13 baseline failures**, 337.79 seconds. This is historical evidence,
  not substituted for the current-base run above.
- Complete local pending-PR combination: **1383 passed**, exit 0, 471.30 seconds,
  `uv run --no-sync pytest -q --basetemp runtime/pytest346-integration-final`.
  Commit `38b51028fe6a93e10a1123e8d6ca123987c87f35`, source tree
  `0f6afbb17bd7eeb52eb2497f8e7ac95d005bdc17`.
- Current-base targeted declaration/provenance/signature/registry/version/deploy
  tests: **332 passed**, including all **74** new provenance cases. The older
  declaration/provenance focused set had 301 passes; it is not a full result.
- Complete-combination focused set: **469 passed**, plus **3** real-crypto
  cross-gate tests. Those 3 local integration-only tests import pending policy
  and snapshot modules, and are not presented as tests shipped in this PR.

The combination contains #332 lifecycle (`25df257`), #338 Lean fixture
(`0f26196`), #340 policy (`e4b9a41`), #342 protocol guard (`d7af106`, now merged),
#344 dependency graph (`4bce12b`), and this provenance implementation. All five
component heads are all ancestors in that local tree; 22 non-doc files
match their component bytes exactly, and the shared registry retains graph
validation plus lifecycle visibility functions by AST comparison. New provenance,
trust, crypto, contract, schema and test files match this branch exactly.
Subsequent #340 `e635045` / #344 `68d4ad8` rebases contain the same component
implementation/tests and only reconcile documentation/base history. This is a
local validation tree, not a protected branch merge or an unmodified-PR result.

The 3 integration-only cases bind the same declaration/manifest digests across
publisher signature, build proof, execution policy and graph evidence. Disabled
graph, disabled execution policy or revoked signer still independently deny.
They do not test or claim a production admission coordinator, which is absent.
Ruff and repeated OpenAPI exact-hash/no-diff checks pass in that combination;
all 11 service smokes and transaction/SIGKILL/crash-recovery checks also pass
there. Initial component Ruff/OpenAPI and current-base rebase checks pass.
Frontend sources were not changed; local npm gates were not rerun for this
backend library, and protected remote CI remains independently required.
