# #343 / #305: immutable global dependency evidence

Baseline: `feature/platform-v1` at `f139973` (includes canonical versions and
service declaration/signature validation). This is a core library, with no new
HTTP API, registry registration, feature flag, database, grant or execution path.

`build_dependency_snapshot([manifest_bytes, ...], disabled=[capability_id, ...])`
checks a complete fixed set and returns deeply frozen manifest identities,
capability records and a content digest. `snapshot.resolve(id, require="^1.0.0")`
returns a dependency-first closure with shared dependencies once. All manifest
inputs are exact UTF-8 bytes; reformatting JSON changes the digest. Reordering
manifest inputs or disabled IDs does not. The digest binds a versioned domain,
every exact manifest-byte hash and the disabled IDs. There is no network lookup,
version selection, fetching, downloading, importing or invoking code.

Construction rejects duplicate JSON members, invalid schema/API version,
unknown fields, namespace or capability conflicts, overlapping plugin namespaces,
missing/incompatible dependencies and global cycles. It reuses the existing
`CapabilityRegistry` entry checks and canonical `satisfies` version implementation.
Dependencies retain the existing stable exact/caret/tilde subset; wildcard and
prerelease dependency constraints are not newly introduced. Root requirements
support the existing canonical version helper, including explicit prereleases.
The single-manifest execution registry still rejects absent dependencies.

Bounds: 32 manifests; 256 KiB each; 2 MiB total; 64 capabilities per manifest;
512 capabilities total; 64 dependencies and 32 permissions per capability;
4096 total edges. JSON parsing errors, malformed values and graph errors return
safe diagnostics without echoing manifest values. Iterative graph traversal and
closure resolution avoid recursion dependence; a 512-node chain and its cyclic
variant are exercised. Byte/node/edge bounds constrain work; this is not a
hard process-memory or CPU isolation mechanism.

Disabled capabilities remain declared in the snapshot. Resolving a disabled
root or any disabled dependency fails, while an independent enabled closure
can still resolve. Missing or incompatible declarations anywhere in the supplied
set fail construction: callers must not hide an invalid graph by choosing a
convenient root. Different disabled IDs produce a different snapshot digest.

## Trust and lifecycle limits

Inputs are supplied by trusted core code, **not verified authorities here**.
A `source=builtin` label conveys no trust. Permissions are declared requirements,
not the current user's scopes. Disabled IDs are fixed caller-supplied data, not
live status. A previously constructed snapshot does not change when a later
snapshot disables a capability. Namespace collision detection does not prove
namespace ownership; no signatures, provenance, current generation, revocation,
policy, approval, lease, service identity or routing authority are validated by
this library. A content digest is evidence identity, not a signature or grant.

Future service admission must bind this evidence to independently verified
manifest sources, current availability/lifecycle revisions and the other
required authorities in an immutable approved plan, then recheck those
revisions at activation and invocation. This PR intentionally adds no such
admission coordinator. Existing UI account discovery, static trusted adapters,
`.xl`, API authentication, deploy receiver and production flags are unchanged.
Rollback is to stop calling the new library; there is no stored migration.

## Validation

Tests cover cross-plugin chains/diamonds, immutable/detached inputs and outputs,
exact-byte digests, deterministic order, disabled transitive dependencies,
independent branches, stable/prerelease and zero-major version gates, namespace
conflicts, cycles, strict JSON, contract rejection, resource bounds, 512-node
traversal and blocked network/process operations. Existing registry and service
contract tests run alongside them. Final commands/results are recorded below.

Real service isolation, deployment, current authority admission and visual or
production acceptance have not been performed. No Issue acceptance, merge,
approval or release is performed by this developer branch.
