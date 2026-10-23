# #341 / #305: reject unsupported deployment constraints

At protected base `2c57f6f`, Deploy Gateway's request models inherited Pydantic's
default extra-field handling. A release containing `service_plan_digest`,
`policy_revision`, `trust_revision`, `generation` or `admission_granted`
was accepted as a normal platform release, with those fields discarded. The
fixed inventory executor similarly ignored extra top-level job/manifest fields.

This is a protocol downgrade/compatibility defect. The caller still needs the
existing release, approval, maintainer identity and exclusive lease permissions.
It is **not evidence that third-party service execution or unauthorized
deployment is currently available**. ADR0010 explicitly requires future plan,
policy, identity and generation binding; unsupported requirements cannot be
silently treated as implemented today.

## Change

All five deployment input models (Artifact, Release, Acquire, Renew, Deployment)
inherit `extra="forbid"`. Unknown top-level or nested artifact fields return the
existing fixed 422 VALIDATION_ERROR response, without creating a release, lease,
audit transition or deployment. Approval and lease release explicitly accept only
an absent body, JSON null or an empty object; nonempty action bodies are rejected
as well. No caller contents are echoed.

The existing identity, approval, lease and unresolved-state prerequisites are
preserved. Before a request can advance approval, acquire a lease, record
`deploying` or dispatch an executor, the gateway revalidates the persisted manifest against the current
Release model. Unsupported or malformed manifests return 409
RELEASE_CONTRACT_UNSUPPORTED; absent releases retain 404 RELEASE_NOT_FOUND.
There is no approval/lease/start transition, deployment row or executor call. This covers stored
data from a newer producer/older consumer and explicit corrupt-state fixtures.
It does not reinterpret or rewrite an approved release.

The actual standalone fixed executor now:

- reads at most 65537 bytes and denies input beyond the existing 65536-byte
  protocol limit, including valid JSON followed by oversized whitespace;
- accepts exactly the current job fields (manifest, deployment_id, epoch) and
  current platform manifest fields (commit, artifacts, migration_manifest,
  test_report), with exact artifact name/digest objects;
- validates known shapes, canonical commit/artifact values and positive integer
  epoch before reading the fixed inventory or attempting SSH;
- keeps exact configured commit/artifact equality, fixed target commands, SSH
  host-key checks and the existing activation/rollback result protocol.

Epoch shape validation is **not target-side durable epoch fencing**, service
attestation or approval of a service plan. New service-plan fields require a
reviewed versioned protocol understood and enforced by gateway, broker and
receiver; they cannot be optional ignored hints.

## Compatibility and rollout

The checked-in deploy_runner emits exactly the existing four release fields,
two-field artifact and current lease/deployment bodies. Its documented requests
remain valid. Response shapes, scopes, approval identities, lease holder/expiry/
epoch checks, target inventory and unresolved-deployment fences are preserved.
There is no database migration, state rewrite, grant, production setting or new
service-execution path.

Clients that send undocumented extras will now fail. A client requiring service
plan/policy/generation enforcement must stop and wait for an actually supported
protocol; stripping its required constraints to make a legacy release succeed
would recreate the downgrade. Existing descriptive-only client metadata needs
reviewed client adjustment. Unsupported stored release manifests remain denied
until an appropriate reviewed supported release/protocol exists.

Deploy API and fixed executor changes together through the maintainer's normal
protected release process. Old consumers still ignore extensions, so this PR
cannot by itself authorize enabling service workflows on a mixed-version fleet.
A code rollback reopens the previous extra-field behavior and needs the same
review; no data rollback is required. No production rollout was performed.

## Regression evidence

The initial regression file reproduced **16 failed / 1 passed** on the unchanged
base, covering silently accepted unknown API fields, stored dispatch and
fixed-executor extra/oversized inputs. A second probe reproduced four additional failures for ignored action bodies
and unsupported stored manifests advancing approval/lease. Final focused tests
record **53 passed**: 31 new protocol cases plus existing deployment, executor and staging cases.

The new tests verify no deployment/audit/executor effects on rejection and fixed
errors without input echoes. They execute the real fixed executor source through
runpy, intercepting every inventory read and SSH subprocess. Synthetic known
inventory and fake SSH outcomes exercise successful activation, confirmed
rollback and unconfirmed failure. Another ASGI case passes the gateway's actual
serialized approved release/lease job through that real fixed executor code and
returns its synthetic completion to the gateway. No SSH target staging receiver
behavior is changed or exercised here; target-side plan/generation verification
and actual activation remain future acceptance gates.

These tests never read the actual /etc inventory or deployment key, contact a
target, approve a real release or deploy code. Synthetic human/maintainer test
identities do not grant the development agent real deployment authority.

OpenAPI export closes the five existing input models, adds the empty action
model and declares optional empty/null request bodies for the two action routes.
A structural comparison removes exactly those changes to recover the original
specification; no endpoint or business field is added. Ruff and diff checks pass. The initial full runs predate the action-body
correction and are not substituted for final corrected results. Full standalone and complete #305 composition results follow below.
Real protected deployment, service plan enforcement, actual network/resource
isolation, real identity provisioning and production/visual acceptance are not
established by this local regression.

## Final corrected full results

The final standalone full suite on `2c57f6f` records **879 passed / 13 failed**,
434.21 seconds. The 13 failed identifiers exactly match the known unchanged Lean
fixture baseline. This branch does not duplicate the separately reviewed #337
fixture repair.

The complete #305 composition, including that repair and all pending adapter,
lifecycle, version, signature and execution-policy components, records **1256
passed**, exit 0, 494.87 seconds. Final composition focus is **217 passed**.
The four deployment source/test/spec files are identical between standalone and
composition; the staged full-run source tree is
`2786b3bce96ad0b942d443105f5f6bf57f980f0a`.

CI-pinned uv 0.12.1 accepts `--locked`. Ruff, all 11 service smokes, transaction/
process/crash fault injection and diff checks pass. Repeated OpenAPI export is
byte-identical to the intentionally updated specs; the structural change is
limited to closed deployment inputs and the optional empty/null actions described
above. No dependency/lock or frontend source change is introduced.

Full-run commands use prepared Python 3.12.14 environments; the standalone env
matches the lock, while the composition environment additionally includes the
pending JSON Schema dev tools. The shared full-run form adds `--no-sync`.

```sh
uv run pytest -q tests/test_deploy_protocol.py tests/test_deploy.py tests/test_executor.py tests/test_deployment_staging.py
uv run pytest --basetemp runtime/issue341-final-full
uv run --locked ruff check xeonolab tests scripts
uv run --locked python scripts/export_openapi.py
uv run --locked python scripts/smoke_services.py
uv run --locked python scripts/verify_failure_injection.py
git diff --check
```

Local composition success is not protected CI approval or actual deployment.
The development agent did not approve a real release, acquire a production lease,
run a real executor/SSH target, merge or publish code, or close an unaccepted
Issue. #305 remains in progress.
