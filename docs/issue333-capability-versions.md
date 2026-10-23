# #333 / #305: capability version admission

At `feature/platform-v1@c1274bb`, the shared comparator stripped every suffix.
`1.0.0-rc.1` satisfied exact `1.0.0`, stable `1.0.0` satisfied exact
`1.0.0-rc.1`, and even malformed `1.2.3-` was accepted as a requirement.
`0.3.0` also satisfied `^0.2.3`. Pending #321 already adds a limited stable
prerelease guard/exact-identity check, but still strips prerelease precedence,
accepts malformed labels and crosses zero-major caret bounds. This increment
finishes the shared contract rather than duplicating its Tool adapter.

## Contract

`xeonolab/capability_versions.py` supplies one bounded parser/comparator;
`capabilities.SEMVER` and `capabilities.satisfies` retain their existing import
paths for adapters and the pending service declaration model. Versions are ASCII
canonical `major.minor.patch`, optionally with nonempty dot-separated prerelease
identifiers. Core numbers and purely numeric prerelease identifiers cannot have
leading zeroes. A version string is at most 256 characters. Build metadata,
partial versions, `v`/`=` prefixes, whitespace, compound ranges and comparator
sets remain unsupported; this is a deliberately bounded subset, not a complete
npm range parser.

Supported requirements remain exact, `^`, `~`, and `*`:

- Exact compares the full canonical release identity, including prerelease.
- Caret preserves the leftmost nonzero core component: `^1.2.3 <2.0.0`,
  `^0.2.3 <0.3.0`, `^0.0.3 <0.0.4`. Tilde stays below the next minor.
- Prerelease precedence compares numeric identifiers numerically, numeric below
  alphanumeric, then ASCII lexical order and identifier count. Stable is above
  every prerelease of the same core version.
- A stable range or `*` excludes prereleases. A range explicitly mentioning a
  prerelease permits compatible prereleases only on that same core tuple; a
  qualifying stable release can still match it.

Precedence/grammar follow [SemVer 2.0.0](https://semver.org/); caret and prerelease
opt-in follow the documented subset of
[node-semver](https://github.com/npm/node-semver#caret-ranges-123-025-004).
These rules do not attest that a publisher's implementation actually preserves
its declared ABI or that a declared package is trusted.

The metadata schema uses the same version expression/length limit. Its stable
manifest-dependency expression is repaired: the old `$` before `@` made every
nonempty dependency fail schema validation. Dependency declarations retain the
existing exact/caret/tilde **stable triplet** subset; prerelease opt-in is supported
by detail requests and trusted adapter requirements, not by a new dependency
syntax. JSON Schema explicitly excludes whitespace so an ECMAScript/Python `$`
match before a trailing newline cannot admit a noncanonical version.

A malformed detail requirement is 422 `INVALID_VERSION_CONSTRAINT`; a valid but
incompatible one remains 409 `CAPABILITY_INCOMPATIBLE`. Authentication,
permission/enablement visibility and 404 are evaluated first. Stable builtin
`1.0.0` bindings retain their results. Existing UI installation snapshots,
preferences, database formats, lifecycle state, permissions and process/executor
boundaries are not migrated. A malformed universal manifest must be corrected
before this stricter registry is deployed; it fails closed with 503, not a
silent reinterpretation. Rollback is a reviewed code/schema revert; no data
rollback is needed.

## Verification

The initial new regression file reproduced 27 failures / 23 passes on the old
comparator before implementation. The final standalone focus suite is 78 passed
(version/schema/detail, existing registry/discovery and snapshot-isolation
cases). This includes a real `Draft202012Validator`, schema self-validation,
checked-in examples/builtins, nonempty dependencies, malformed identifiers and
newline rejection. `jsonschema>=4.23,<5` is a **dev-only** dependency; production
runtime dependencies do not change.

CI pins uv 0.12.1 and Python 3.12.13. Locally uv 0.12.1 accepted the updated lock,
and a new Python 3.12.14 environment was created with `uv sync --locked`.
Its 78 focused cases, CI-scope Ruff and OpenAPI export pass. All 44 installed
locked package versions exactly match the environment used for the full run.
Full standalone regression: 793 passed / 13 failed, 287.35 s; failed identifiers
were compared as a set against the reproduced baseline's same 13 Lean sandbox
failures. No test was skipped or sandbox check weakened.

Commands (the local shared full-run environment adds `--no-sync`):

```sh
uv sync --locked
uv run pytest -q tests/test_capability_versions.py tests/test_capabilities.py tests/test_plugin_snapshot_isolation.py tests/test_capability_desktop.py
mkdir -p runtime
uv run pytest --basetemp runtime/issue333-test-tmp
uv run ruff check xeonolab tests scripts
uv run python scripts/export_openapi.py
git diff --exit-code -- docs/api
uv run python scripts/smoke_services.py
uv run python scripts/verify_failure_injection.py
```

No frontend source, database migration, runtime code loading, deployment flag or
account grant was changed. Real provider/GPU/Qdrant, OIDC/multihost, image builds,
visual acceptance, protected CI and production acceptance are not established by
these local synthetic tests.

## Composition evidence and correction

The previous #327/#332 integration tree `4050e414` contained the four adapter
entry points but omitted #321's registry dependency/budget validation and its
`test_capability_execution_contract.py`. Its 867/13 result remains an entry-point
composition result, **not** proof of the full PR stack. That scope correction is
recorded on #305 comment 3574. No branch's independent tests are replaced by it.

The new integration tree starts at c1274bb and inventories all 31 non-document
source/test/schema files changed by #321/#323/#325/#327/#330/#332. Every
single-owner file was checked byte-for-byte against its PR head, except the
explicitly changed version registry and regenerated service schema. Shared
`services.py` preserves the trusted Tool/Chat adapters and pre-body authority
checks; `search.py` preserves the live checkpoint and trusted chunker. #321's
strict registry validation is retained, while its older comparator is replaced
by the new shared parser. Integration-only cases exercise all four real adapter
resolvers and reject zero-major/prerelease dependency confusion; no provider or
processor needs production credentials.

The service declaration schema from #330 changes because its Pydantic version
field references `SEMVER.pattern`. The old generated artifact was deliberately
tested first: 129 pass / one schema-drift failure. Regenerating it in the composed
source fixes that expected drift; it must be included when #330 is rebased onto
this change. Reproduction, with #330's source present:

```sh
uv run python -c 'import json; from pathlib import Path; from xeonolab.service_plugin_contract import declaration_schema; Path("infra/plugins/service-plugin-declaration.schema.json").write_text(json.dumps(declaration_schema(), indent=2) + "\n")'
uv run pytest -q tests/test_service_plugin_contract.py
```

The schema/adapter/execution-contract focus recorded 157 passes. The broader
complete composition focus recorded 309 passes. Full composition and rollback
flags are recorded below; no earlier partial tree is substituted for them.

Tooling notes: an initial offline dependency resolution could not find cached
FastAPI metadata; ordinary TLS-verified online resolution succeeded. System
Python lacks FastAPI, so actual checks use the prepared 3.12 environments.
`require.resolve('semver')` returned MODULE_NOT_FOUND; conformance vectors were
checked against the primary specification/docs above rather than treating that
unavailable package as an oracle. No TLS verification was disabled.


The complete composition full run finished with **1024 passed / the identical
13 baseline Lean failures**, 316.71 s; staged index tree
`a1a7870d80251a67eb5d25623134dc7c8cc680bb`. The failure-name set comparison
passed. All four adapter flags set to 0 before startup: 49 lifecycle cases pass.
This supersedes the earlier partial-tree claim; the stricter #321 validation and
its 17 execution-contract cases are now included, alongside #330's contract.

While this validation ran, the maintainer merged #330 into protected base
`ef4996b`. This PR is rebased onto that base, and the generated service
schema is included directly rather than leaving a future rebase task. The
rebase does not approve/activate a service runtime. The new-base standalone
regression result is recorded below.


The service declaration field now shares the 256-character version bound and
explicit schema whitespace exclusion. Eleven constructor/JSON-Schema cases
cover canonical prereleases, the 256/257 boundary, malformed numeric identifiers,
newlines and unsupported build metadata. No trust, admission or execution
permission is added by these shape checks. The final new-base full runs include
this consumer change and its regenerated schema.


## Final new-base results

On `ef4996b`, using CI-pinned uv 0.12.1 with `--locked` and the fresh 3.12.14
environment, full standalone regression recorded **861 passed / the same 13
baseline failures**, 441.10 s. The complete composition with all pending trusted
adapters, strict registry dependency/budget validation and service declaration
consumer bounds recorded **1035 passed / the same 13 baseline failures**,
482.66 s. Both failure-name set comparisons pass. Its full-run staged source
index is `3910ad2ed0340baea2968939fcf5501528fa3be2`.

The Docker-COPY probe initially found an existing base recipe omission: no
public capability manifest was copied, causing registry load to fail with 503
inside the staged layout. This PR reuses the identical manifest COPY line
already present in the pending adapter PRs. A permanent isolated layout test
imports the copied package, loads its copied public registry, and rejects the
prerelease/stable mismatch; it cannot fall back to a checkout manifest. This
additional packaging case was added after those full runs and is included in
the final standalone focus: **147 passed**. It is a COPY-layout regression, not
an image build/container isolation or production acceptance claim.

CI-scope Ruff, unchanged OpenAPI export, lock consistency, 11 local service
smokes and process/transaction/crash injection pass on the final new base. The
complete-composition rollback configuration (all four local adapter flags 0
before startup) records 49 lifecycle passes. No protected CI success is inferred
from these local runs, and no merge, approval, release or production flag/grant
change was performed.
