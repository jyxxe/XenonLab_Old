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

Construction rejects duplicate JSON members, invalid core metadata/API version,
unknown top-level/entry fields, namespace or capability conflicts, overlapping plugin namespaces,
missing/incompatible dependencies and global cycles. It reuses the existing
`CapabilityRegistry` entry checks and canonical `satisfies` version implementation.
Dependencies retain the existing stable exact/caret/tilde subset; wildcard and
prerelease dependency constraints are not newly introduced. Root requirements
support the existing canonical version helper, including explicit prereleases.
The single-manifest execution registry still rejects absent dependencies.
The core projection preserves its optional empty dependencies and 30-second
timeout defaults. Display metadata is unused here and retains legacy registry
validation; this graph builder does not replace strict service declaration/schema
validation or attest that any supplied manifest was signed.

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

### Final local evidence

Functional commit: `ac965c4`, unchanged during final tests. Fresh Python 3.12.14
environment synced using CI-pinned `uv 0.12.1` and `uv sync --locked`.

- `uv run --locked pytest -q` on the original branch, with
  `--basetemp runtime/pytest343-raw-rerun`: **1115 passed / 13 failed**,
  exit 1, 436.82 seconds. Failed test names exactly equal all 13 recorded
  baseline Lean fixture failures, not a selected subset or a new exclusion.
- Independent worktree at the same functional commit, differing only in the
  exact `tests/test_lean_verifier.py` from PR #338 (`0f261962a1ac`):
  `uv run --no-sync pytest -q --basetemp runtime/pytest343-fixture-rerun`:
  **1135 passed**, exit 0, 440.07 seconds. Staged source tree
  `2a32efc054968969afc01730e71bc6bb9cd8dfde`.
  This fixture-only control is not the unmodified branch and does not include
  other pending lifecycle, execution-policy or deployment-protocol PRs.
- Locked targeted registry/version/service contract/signature/adapter tests:
  **383 passed**. Includes real Ed25519 signatures and revocation: neither
  signature nor fixed graph evidence replaces the other gate.
- Actual builtin manifest exact-byte composition probe: 28 builtin capabilities
  plus one declared service form 29 entries; service-to-builtin closure resolves
  both entries and retains the exact builtin-byte hash. No registration occurs.

The first full attempts were interrupted and are **not acceptance results**:
the new worktrees lacked the `runtime/` parent of the requested basetemp. A
single-test reproduction confirmed `FileNotFoundError` creating a basetemp under
a missing parent. After explicitly creating both parents, the new final run
logs/directories above were used. A diagnostic run was also interrupted after
461 passes; it is not a full result. No test skip/xfail or production sandbox
weakening was added to remedy this command setup error.

`uv run --locked ruff check xeonolab tests scripts`, repeated
`uv run --locked python scripts/export_openapi.py` with exact hash comparison
and no `docs/api` diff, all 11 `scripts/smoke_services.py` service checks, and
`scripts/verify_failure_injection.py` transaction/SIGKILL/recovery scenarios
passed. `git diff --check` passed. Frontend sources were not changed; npm gates
were not rerun locally for this backend library. Protected remote CI remains
an independent review requirement.
