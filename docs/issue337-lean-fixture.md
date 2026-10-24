# #337 / #305 regression gate: deterministic namespace test seam

The Lean unit suite models root-owned toolchain files and distinct network/mount
namespace inodes through the existing `lean_verifier._stat` seam. Its old fixture
called host `os.stat` before replacing namespace metadata. On an ordinary user
with restricted PID 1 namespace visibility this raised PermissionError before
the intended synthetic inode could be returned. Functional cases therefore
received 503 instead of exercising the fake Lean child.

On protected base `529e335`, the unchanged suite reproduced **13 failed / 10
passed**, matching the previously recorded baseline identifiers. This is a test
environment dependency, not permission to relax the real sandbox prerequisite.

## Change and security boundary

One test-only helper recognizes exactly the four net/mnt namespace observation
paths. It supplies synthetic separated inodes directly. Other paths still use
real filesystem metadata for fixture file type/mode/size, with the existing
simulated root ownership. Both existing fixture restoration sites reuse it.

An additional positive integration case supplies a stat reader that would reject
every /proc access, verifies readiness and a real fake-toolchain subprocess,
and asserts only actual toolchain fixture files reached that reader.

Six negative cases exercise the **production** namespace detector and API
readiness/admission with shared inodes, PermissionError or FileNotFoundError,
independently for net and mnt. They verify 503 VERIFIER_UNAVAILABLE. The detector
itself is not stubbed in these cases. Missing isolation remains a failure.

No test is skipped or marked expected-failure. No production Python, service
configuration, namespace check, executor, deployment setting or dependency file
changes. Test results do not prove actual sandbox/network isolation or a real
Lean installation; those remain #28 deployment acceptance.

## Validation

Focused suite after the change: **30 passed** (23 original + 7 additional).
CI-scope Ruff and OpenAPI export without drift pass. Production paths and
dependency files have an empty diff against the base.

Commands use prepared Python 3.12.14 and CI-pinned uv 0.12.1:

```sh
uv run pytest -q tests/test_lean_verifier.py
uv run pytest --basetemp runtime/issue337-full
uv run ruff check xeonolab tests scripts
uv run python scripts/export_openapi.py
git diff --exit-code -- docs/api
git diff --exit-code origin/feature/platform-v1 -- xeonolab infra scripts pyproject.toml uv.lock
```

The complete #305 composition is also run with the identical test file in a
separate basetemp. Full terminal results are recorded after completion.

Standalone full result on the protected baseline: **846 passed**, exit 0,
427.71 seconds. All original Lean tests execute, including the former 13
environment-dependent failures. A fresh environment from `uv sync --locked`
also passes all 30 Lean cases. Every locked package version in that fresh
environment matches the shared full-run environment (which additionally has
the unrelated JSON Schema development tools needed by the composed branches).

Complete #305 composition result: **1141 passed**, exit 0, 480.90 seconds.
This includes the pending LLM/Embedding/chunker adapters, durable lifecycle,
strict execution dependency checks, canonical versions, offline declaration and
DSSE/public-trust component, with the identical test-only fix. It supersedes the
previous 1121-pass/13-baseline-failure result for this specific composed source
plus seven new regression cases; it is not evidence that pending PRs merged or
that protected CI/production/runtime acceptance completed.

Only the test fixture and this evidence document are submitted in #337. No
production change is needed to eliminate the environment-dependent failures.
