# #346 / #305: source-build provenance verification

Baseline: `feature/platform-v1` at `f139973`. The existing declaration verifier
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
text, consistently enforcing the intended byte contract. No valid UTF-8 service
input changes. Existing dependency/timeout/namespace and signature checks remain.

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
