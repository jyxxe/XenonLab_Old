# #335 / #305 Phase 3: service declaration signatures and public trust state

Base: `feature/platform-v1@ef4996b`. ADR 0010 remains a proposed runtime design.
This increment implements a cryptographic/authority component for that design;
it does not admit, register, stage or execute a service. No application imports
or initializes the new store at startup, no HTTP endpoint or account scope grant
is added, and production trust roots/configuration remain untouched.

## Signed object and verification profile

The signature covers the **exact UTF-8 service-declaration bytes**, including
its artifact digest, exact capability-manifest byte digest, source commit and
execution-policy digest reference. `validate_service_plugin` remains the
application parser: no duplicate members, invalid types, changed manifest bytes,
URL/tag/command substitution or incompatible declaration shape is accepted.
A valid signature authenticates that signing claim; it does not prove that the
OCI bytes exist, were built from the source, are harmless, or satisfy the policy.

The reviewable initial profile is standard
[DSSE protocol v1.0.2](https://github.com/secure-systems-lab/dsse/blob/master/protocol.md)
and its [JSON envelope](https://github.com/secure-systems-lab/dsse/blob/master/envelope.md),
using [cryptography Ed25519 verification](https://cryptography.io/en/stable/hazmat/primitives/asymmetric/ed25519/).
No curve/signature primitive is implemented here. Cryptography was already
locked through PyJWT[crypto]; its existing 50.0.1 selection is unchanged and is
now also an explicit direct dependency (`>=45,<51`). No signing/private-key API
is introduced in production code. Tests generate temporary keys in memory.

Profile details:

- Fixed payload type `application/vnd.xeonolab.service-plugin-declaration.v1+json`.
  DSSE PAE uses UTF-8 **byte** lengths and binds type plus payload. The parser
  receives the same decoded bytes that were verified; it never reloads the
  envelope to select another payload.
- Standard and URL-safe Base64, with normal or omitted padding, are supported.
  JSON is bounded and rejects duplicate members/non-finite constants. Envelope
  limit is 64 KiB, declaration 16 KiB, manifest 256 KiB, at most 16 signatures,
  and at most 512 decoded bytes per signature. Only 64-byte Ed25519 signatures
  can count. Decoded unsupported signatures and invalid/unknown signers cannot
  inflate the threshold; malformed encoding fails verification.
- Unknown envelope/signature extension members are ignored as DSSE specifies.
  They are not interpreted as roots, URLs, paths, algorithms or commands.
  Optional/empty `keyid` is only a candidate hint. If present in this profile it
  selects a SHA-256 public-key fingerprint, and the actual signature still has
  to verify against that public key.
- The threshold counts distinct verified public-key material, not envelope
  entries or key-id strings. All counted keys must currently authorize the
  declaration's exact plugin id. A prefix-similar id is not ownership.

## Core-owned trust database

`TrustPolicyStore` takes a fixed **core-selected** SQLite path. It is not derived
from an envelope, declaration, plugin install or publisher URL. Constructor
flags `initialize=False` and `management_enabled=False` are the defaults. An
uninitialized/missing store, revision 0, malformed/expired policy or unreadable
revocation state fails closed; read-only verification cannot create a database.

Explicit provisioning creates namespaced trust tables only in the selected
database. The component does not migrate account/catalogue/`.xl` databases.
Policy publication calls the existing `common.identity(request)`, requires
explicit `plugin-service:manage`, a human identity under the existing platform
identity semantics (API keys are rejected), and the separate core management gate. This permission is not added
to production accounts or the account-scope vocabulary by this PR. There is no
write CLI/HTTP route and no publisher-controlled management flag. This does not introduce a fresh-login/MFA ceremony beyond the existing identity
semantics. Future host and authorization provisioning needs its own protected
implementation/review.

Policies have a schema version, issue/expiry time (at most 24 hours), threshold
1–16, and at most 32 public Ed25519 keys. Each key has exact plugin-id ownership
and current-time validity bounds. Builtin namespaces are reserved. Duplicate
public-key material cannot be added under a new label/encoding. The policy
schema is generated at `infra/plugins/service-signature-trust.schema.json`;
semantic validation additionally enforces times, unique material, ownership
and valid decoded key lengths. The `service-signature-trust-deny.json` example
contains no keys and deliberately expired placeholder times; it enables nothing.

Publication uses CAS on a monotonic signed-64-bit revision, UUID operation id,
exact policy-byte digest and an audit row in the same existing transaction
helper. Same actor/id replay returns current revision plus the historical
applied revision; different input conflicts. A replay cannot restore an older
policy. An audit failure rolls back the new policy, revision and revocations.

Key fingerprints and artifact digests have permanent revocation tombstones,
even if a later policy omits them. An active revoked key cannot be restored by
re-encoding/renaming its public material. Artifact revocation rejects that
**declared digest**, including when another authorized key signs it. There are
at most 4,096 total tombstones; overflow safely rolls back rather than dropping
a revocation. Audit/history retention and future migration must preserve the
required evidence/tombstones; no destructive compaction is implemented here.

Every verification reads a fresh bounded SQLite snapshot, checks policy/key
validity and optional core-provided revision floor, performs crypto and contract
validation, then rereads current trust state. A concurrent policy/revocation
change or expiry prevents stale evidence from being returned. Read connections
are explicitly closed. State is not cached as an allow decision. Disabling the
management gate does not disable existing revocations.

## Evidence and admission remain separate

`SignatureEvidence` contains declaration/manifest/declared-artifact digests,
verified public-key fingerprints, trust revision/digest, check time and a bounded
validity ceiling. It contains no user identity, bearer token, permission grant,
service route, lease or executor handle. `valid_until` is not a promise against
future revocation. Future admission must reverify against current authoritative
trust and bind those immutable inputs to policy/provenance, caller permissions,
generation and protected deployment approval; it must not accept a serialized
receipt or cached CLI success as an authorization credential.

The final snapshot read is the verification point. A revocation after that read
invalidates the evidence for later admission. There is no atomic cross-service
routing transition, publisher build attestation, signing timestamp, transparency
log, issuer/grant exchange, OCI retrieval/digest validation, network/quota
sandbox, runtime lifecycle or deployment/rollback controller in this increment.

Custody of the fixed trust DB and clock is a core/maintainer assumption, not
filesystem isolation proven by this library. The SQLite audit/digest is not a
tamper-proof external ledger. A privileged replacement with an old but unexpired
DB cannot be detected without a separate trusted monotonic checkpoint; callers
must supply a current external revision floor where rollback is possible.
Expiry/floor checks do not establish distributed revocation freshness by
themselves. Root bootstrap/distribution, secure ownership/permissions, checkpoint
custody and actual service runtime acceptance remain explicit follow-up gates.

## Offline operator tool

The CLI only reads explicitly selected local files and an existing trust DB:

```sh
uv run python scripts/verify_service_signature.py envelope.json manifest.json --trust-db /core-controlled/service-trust.sqlite --minimum-revision 7
```

The operator must establish that DB/path/floor out of band; a publisher-selected
DB is not platform authority. Output names the actual signature check, declares
`admission_granted=false`, and includes bounded digests/revision/fingerprints.
Errors do not echo raw JSON, filenames, credentials or upstream details. The CLI
cannot initialize/mutate the store, download an artifact or execute a service.

## Validation

Final focused signature/contract/auth/deploy regression: **145 passed**. It covers the
published PAE example and UTF-8 length vector, real Ed25519 signatures, standard
and URL-safe Base64, absent/forged key hints, byte/type/commit/namespace/policy/
artifact substitutions, exact ownership, threshold duplication, unknown and
unsupported signatures, permanent key/artifact revocation, key/policy/floor/time
failures, duplicate/oversize/malformed JSON, unavailable clock, read handle
closure, default-off/empty/missing trust, two-writer CAS, concurrent revocation
and expiry during crypto, transactional audit/quota failure, CLI redaction and
no network/process/registry mutation. Keys are generated only for synthetic
cases; no credentials or private key material are checked in or installed.

A fresh Python 3.12.14 environment was synced with CI-pinned uv 0.12.1 and the
locked file. Initial regression before the dependency-composition correction below: **873
passed / 13 failed**, 288.43 s. The failed
identifier set is checked against the reproduced baseline Lean namespace
permission failures; no skip or sandbox weakening is used. Final full-run and composition evidence are recorded after the correction below.

Commands:

```sh
uv sync --locked
uv run pytest -q tests/test_service_plugin_signatures.py tests/test_service_plugin_contract.py tests/test_auth.py tests/test_deploy.py
mkdir -p runtime
uv run pytest --basetemp runtime/issue335-test-tmp
uv run ruff check xeonolab tests scripts
uv run python scripts/export_openapi.py
git diff --exit-code -- docs/api
uv run python scripts/smoke_services.py
uv run python scripts/verify_failure_injection.py
```

No frontend code or OpenAPI endpoint changed. Real OIDC/human provisioning,
production trust roots, external checkpoint/distribution, independent build
provenance, real OCI/sandbox/quotas/network, image builds, visual acceptance and
production activation have not been performed. Protected CI and maintainer
security review remain separately required. This is not Phase 3 completion.


## Offline dependency composition correction

A real composition discrepancy was reproduced before finalization: the same
shape-valid foreign dependency passed the standalone offline contract validator
but failed after #321's stricter execution registry was composed. ADR 0010
explicitly defers external dependency compatibility to eventual global admission.
The offline contract now validates a private local-dependency view while keeping
the original manifest bytes/digest and declaration unchanged. It checks local
missing/incompatible/cyclic references; foreign namespace references remain
unresolved. Manifest dependency requirements retain the existing canonical
stable-triplet subset, and stable local requirements reject prerelease targets.
The shared canonical comparator from #334 remains the version implementation in
the full composition; this increment does not add another semver implementation.

This private view is neither returned nor registered. The execution registry's
strict missing/version/budget checks remain unchanged and their original tests
still run in the complete composition. Signature evidence cannot satisfy global
dependency admission. A future coordinator must use the **original** digest-bound
manifest to resolve every external version, permission, disabled state and cycle
against one immutable global registry snapshot before enabling any service.

The corrected signature/declaration/execution-registry/Tool/version focus is
**242 passed** in the complete composition. It includes a signed foreign-reference
case whose returned digest still matches original bytes, local missing/version
rejections and stable dependency grammar. The broader initial signature/authority
composition had 297 passes. Initial full composition before this correction was
1115 passes / the same 13 Lean baseline failures, 332.21 s; it is historical
scope evidence, not substituted for the final corrected full runs.

Final corrected full runs use separate clean basetemp directories and the same
locked dependency versions. Ruff, OpenAPI and platform smoke/failure-injection
checks are repeated on the final sources as recorded below.


## Final corrected regression evidence

After the offline-dependency correction, full standalone regression recorded
**879 passed / the identical 13 baseline Lean failures**, 444.34 s. Complete
composition of the trusted adapters, strict execution registry, lifecycle,
canonical versions, service declarations and this signature component recorded
**1121 passed / the identical 13 baseline failures**, 491.64 s. Both failure-name
set comparisons passed. Interrupted/older runs are not substituted for these
corrected terminal results. Final focus is 145 signature/contract/auth/deploy
cases and 242 signature/contract/execution-registry/Tool/version cases in the
composition. Ruff, locked consistency, OpenAPI without drift, 11 service smokes
and transaction/process/crash injection pass. The generated policy schema and
deny example were also checked with a real Draft202012 validator in the prepared
schema environment (system Python lacks jsonschema).

The maintainer then merged #321 at protected base `529e335`; this feature is
synchronized onto it before publication. The composition already includes that
strict registry, and rebase-specific validation is recorded separately. No
runtime is enabled and no policy/root/grant is installed by the synchronization.
