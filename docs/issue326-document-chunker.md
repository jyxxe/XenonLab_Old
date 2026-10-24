# #305 Phase 2 / #326: trusted XeLibrary text chunker extension

## Contract and default compatibility

`ChunkerBinding` registers a trusted synchronous text processor, derivative
identity, capability contract and resource budgets. `DocumentChunkers` resolves
`builtin.platform.worker.document-chunker@^1.0.0` (worker/api) with the existing
reindex minimum `xelibrary:admin`; it does not add read/write scopes to an
admin-only principal. No manifest string selects executable code. No registration,
file access or arbitrary code-execution HTTP API is added.

The default binding delegates to existing `chunk_documents` / `chunk_tokens` and
keeps `chunker-v2`. Context, overlap, token windows and default deterministic UUID5
point identities remain. Default stored vectors do not require a migration or
rebuild merely because this adapter is enabled. This is a first text indexing
extension, not a PDF/attachment parser, model change or vector-space migration.

A processor sees a readonly snapshot of nonempty string fields from a row that
the kernel has already authorized, plus tokenizer/path/table context. Numeric,
empty and nested values are not supplied. It receives no database, Request,
identity object or credentials. Outputs must identify a supplied source column
and contain nonempty bounded text within the existing 126-content-token encoder
budget. They are derivatives, not authoritative facts.

## Derived identity and read gates

A different processor cannot reuse an already registered identity in the same
registry. Trusted replacement must occur before first index/read use; after that
the selected binding is pinned until service restart. Every semantic change to
an implementation/configuration must have a new derivative identity. This is a
trusted author contract, not executable-code attestation or package signing.

The selected identity enters point UUIDs, SQLite `chunker_version`, Qdrant payloads,
SQLite bootstrap selection, ANN candidate/local-reference validation and final
quality/cache revalidation. A new binding therefore rejects old derivatives
until an authorized rebuild. It does not silently relabel or delete source facts.
Reindex retains the existing row revision/path/ACL recheck, table transaction,
Qdrant upsert and obsolete point cleanup. Failed output or budget checks occur
before publishing that row, so previously committed row indexes remain.

SQLite bootstrap snippets now come from the current authorized source column,
matching the existing Qdrant/final quality behavior, rather than from processor
output. A synthetic transformed index body cannot become a presented fact.

The index-status endpoint retains its existing **row revision coverage** meaning;
it deliberately does not fold model/chunker staleness into those counts. Semantic
search already rejects mismatched identity and reports compatible-result gaps.
Operators must explicitly reindex after changing the binding.

## Budgets, enablement and failures

Defaults are 10,000 chunks per row, 32,768 characters per chunk, 126 tokens per
chunk and 10 seconds of cooperative processor/validation time per row. This adds
finite output/processing limits to previously unbounded chunk production; a very
large legitimate row can fail its job instead of being silently truncated.
Trusted bindings can tune limits within validation bounds (up to 1,000,000 chunks,
32,768 characters, 126 tokens, 300 seconds); effective processing time is capped
by the capability declaration as well. No production policy was changed here.

Time accounting includes processor construction, iterator steps, tokenizer
validation, exhaustion and successful cleanup; it excludes caller embedding I/O
while the iterator is suspended. Checks occur only when trusted code returns.
They cannot interrupt an infinite CPU loop or blocking `next()`. Hard process/CPU
isolation remains Phase 3, not a Python-thread timeout claim.

Kernel-only `set_enabled(False)` refuses new index processing. In-flight work is
checked at iteration and before the row publication transaction; stopping during
processing causes a failed job rather than overwriting that row's old index.
It is not a transactional global revocation protocol. Reading committed vectors
with the selected identity does not invoke the processor, so it remains possible
while the chunker is disabled. Enabling/disabling does not remove persistent
facts or automatically purge valid derivatives.

Malformed output, unknown source columns, token/character/count overruns,
cooperative timeout and processor exceptions fail closed. Public admission
errors use capability codes; background jobs retain the existing safe generic
failed-job message. Processor/cleanup details are not returned or logged. Cleanup
failure after normal exhaustion prevents publication; cleanup during another
failure does not hide the original failure. Existing index locks/clients are
released through the original worker finally block.

## Rollback and limits

`XELIBRARY_CHUNKER_CAPABILITIES=1` (default) enables capability scope/version and
local enablement checks. Trusted configuration `0` bypasses those gates while
retaining the selected binding, identity discipline, output guards and source
checks; a full code revert restores direct processing. Invalid flag values fail
startup. No source `.xl` schema change, data migration, new caller scope or
production deployment/configuration change is introduced.

Local flags are process-local and do not synchronize desktop aggregate builtin
metadata; persistent cross-service authority is supplied by PR #332 as described below. Third-party
code/service deployment, package signing and hard quotas remain Phase 3. The
Docker recipe includes the required public manifest, but an image build and
production model/Qdrant/visual acceptance have not been performed.

## Local validation

Python 3.12.14, repository dependencies, no lockfile changes:

- `uv run --no-sync pytest tests/test_document_chunker.py tests/test_search.py tests/test_qdrant_search.py tests/test_index_status.py tests/test_quality_search.py`: 77 passed.
- `mkdir -p runtime` then `uv run --no-sync pytest --basetemp runtime/test-tmp`:
  744 passed, 13 failed in 277.43s. The exact failure set matches the unchanged
  baseline's 13 Lean verifier failures. This account cannot stat `/proc/1/ns/net`
  or `/proc/1/ns/mnt` (PermissionError 13); no Lean code/test or sandbox gate changed.
- `uv run --no-sync ruff check xeonolab tests scripts`: passed (CI scope).
- `uv run --no-sync python scripts/export_openapi.py`: passed; generated changes
  only document reindex admission 404/409/503 responses.
- `uv run --no-sync python scripts/smoke_services.py`: all 11 services passed.
- `uv run --no-sync python scripts/verify_failure_injection.py`: all passed.
- PR #321 strict validator resolves the new capability with admin-only scope.
- Staged Docker COPY layout resolves the new capability in isolated Python.
  This is not a Docker build or container run; Docker CLI was unavailable on the
  development host (exit 127), and production validation was not performed.
- `git diff --check`: passed. No real model/Qdrant, frontend or visual acceptance
  performed. New integration tests use synthetic embeddings and mock Qdrant.


## Baseline refresh after desktop discovery (#319)

Rebased onto `feature/platform-v1@acc52f3`. Every current builtin UI entry
is retained exactly, as is every previous backend entry; the capability id set
is the exact union without duplicates. Backend provider/chunker source files
are byte-identical to the pre-refresh backup; this fixes the manifest conflict
without changing their implementations. Only this Agent's feature branch is
updated, never the protected base.

Current validation: **88 focused tests pass; 747 full-suite tests
pass / 13 baseline Lean failures in 431.63s**. The exact failed-name set
matches the previously reproduced unmodified baseline (`/proc/1/ns/net`, `mnt`
stat PermissionError 13). No sandbox protections were changed or skipped.
CI-scope Ruff, OpenAPI export (no extra generated diff) and diff check pass.
Earlier counts above refer to earlier snapshots and selected test sets.

```sh
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest tests/test_document_chunker.py tests/test_search.py tests/test_qdrant_search.py tests/test_index_status.py tests/test_quality_search.py tests/test_capabilities.py -q
mkdir -p runtime
UV_PROJECT_ENVIRONMENT=../issue305/.venv312 ../tooling/bin/uv run --no-sync pytest --basetemp runtime/rebase-test-tmp
```

No real model/production/visual acceptance, deployment, approval or merge was
performed. Current protected CI and maintainer acceptance remain outstanding.


## Authority interlock with #331 / PR #332

The index root and document-chunker child are checked independently of the local
adapter flag. Strict lifecycle mode requires both a verified identity snapshot
and the kernel's live job checkpoint; a missing authority/helper is 503 rather
than a local fallback. With the updated authority, a job refreshes at worker
start and at cooperative checkpoints at most once every five seconds. The
returned identity is used for subsequent source ACL checks; declared processor
permissions are rechecked too. The core retains only the original caller's
minimal in-memory authorization context. A processor never sees this context,
Request, identity, database or credentials; the context fields are excluded
from resolved-binding repr. Queue storage contains no credential.

The companion authority controls `builtin.platform.worker.index`; its denial
also hides the document-chunker child. Disabling is cooperative, with no hard
wall-time, cross-database atomic revocation, process kill or purge claim. Existing
matching committed derivatives and source CRUD/job views remain readable.
A five-second polling interval can be delayed by blocking code/I/O. Original
caller expiry, scope loss, invalid snapshot, authority failure or rate-limit
rejection safely fails the job. Remote authority rate limits remain enforced;
internal continuation checks do not consume repeated inbound bulk admissions.

This branch preserves pre-rollout compatibility with lifecycle mode 0 and an
old authority, but once a verified snapshot is supplied it cannot be bypassed by
`XELIBRARY_CHUNKER_CAPABILITIES=0`. The complete strict/live behavior requires
PR #332; deploy all snapshot consumers before enabling mutations. No production
rollout, real model/Qdrant acceptance or hard process isolation was performed.


Authority integration validation: final focused chunker/index/search/Qdrant
suite 50 passed. Full regression recorded 751 passed and 13 failures (420.35 s),
with the failed identifiers exactly matching the baseline Lean namespace
permission failures; the additional local-only publication regression was also
included in the final focused run. Ruff, Python 3.12 OpenAPI export, 11 local
service smokes, diff review and isolated Docker-COPY layout checks pass. No real
Docker image or production/model/Qdrant/visual acceptance is claimed.
The c1274bb combined tree (#325/#327/#332 with native LLM, merged #328 preserved)
passes 175 focused tests and 15 startup chunker-flag-0 authority tests. Full
combined regression is tracked separately in the companion lifecycle record.


Final combined full regression on `c1274bb`: #325/#327/#332 with native LLM
recorded 830 passed / the same 13 baseline failures (437.93 s), staged source
index tree `4c0a34a8bf5411f5b8cebf6da8aa95675b5f425b`. An entrypoint-only Tool
and Chat Provider composition from #321/#323 recorded 867 passed / the same 13
baseline failures (405.01 s), staged source index tree
`4050e4142b7b009cf690ee450199aebd21edf023`. All failure identifiers were compared
as sets against the reproduced baseline. The verifier and its tests remain
byte-identical to baseline c44076a; direct stat probes of `/proc/1/ns/net` and
`/proc/1/ns/mnt` both returned PermissionError errno 13 on this desktop.

That entrypoint-only tree has 229 passing focused cases and 49 passing lifecycle
cases with all four adapter flags set to 0 **before startup**. Its 28 unique
capability declarations retain all five controlled roots and encode/chunker
children; trusted Tool/Chat/Embedding/Chunker modules and the public registry
resolve in an isolated staged Docker-COPY layout. Ruff, regenerated OpenAPI,
11 service smokes, and transaction/process/crash failure injection also pass.
These staged integration sources are review/test compositions, not a protected
branch merge or deployment. Protected CI remains independently required.


Correction (#333 / PR #334): the older `4050e414` composition omitted #321's
registry dependency/budget validation and `test_capability_execution_contract.py`.
Its 867/13 result is not proof of the full PR stack. This limitation is recorded
on #305 comment 3574. The corrected composition inventories all 31 non-document
files of the six PRs, retains that strict validation and adds PR #334's canonical
version gate and service-schema update. Its final full run records 1035 passes
and exactly the same 13 baseline Lean failures, source index
`3910ad2ed0340baea2968939fcf5501528fa3be2`; final focused suite including the added
COPY regression is 321 passed. All four local adapter flags 0 before startup:
49 lifecycle cases passed. See PR #334 / docs/issue333-capability-versions.md for
scope, the 861/13 standalone run on ef4996b and the generated schema/probe details.
This composition includes the pending version fix; it is not a protected merge
or production acceptance claim for any individual PR.


Rebased onto maintainer base `ef4996b` after #330 merged. The ADR conflict was
resolved by retaining both the Phase 3 proposal link and this feature section;
own implementation files were compared byte-for-byte to the prior PR head and
are unchanged. Targeted feature/registry/discovery/snapshot/service-contract
regression: **129 passed**. CI-scope Ruff, regenerated OpenAPI without
working-tree drift and diff checks pass. This rebase did not repeat each old
full suite; the complete 1035/13 composition including PR #334 is separate
evidence and its same 13 Lean baseline failures remain disclosed. No production
flag, permission, merge or deployment changed.


## Synchronization after Tool adapter merge

Rebased onto protected base `529e335` after #321 merged. Independent ADR
sections and Tool manifest entries are retained. Feature-specific focused tests
plus Tool adapters, strict execution registry and service declaration checks pass: **112 tests**. Ruff, OpenAPI export without drift and diff checks pass.
The earlier full regression remains scoped to its recorded source baseline; no
new individual full run is claimed for this synchronization. Production/visual
acceptance and merge remain outstanding.


## Synchronization after Chat Provider merge

Rebased onto protected base `2c57f6f` after #323 merged. The Chat Provider
manifest and async JSON/SSE implementation are preserved. Focus including this
feature, Tool/strict registry/declaration and Chat Provider/native LLM cases:
**151 passed**. Ruff, OpenAPI without drift and diff checks pass. Earlier full
runs keep their recorded scope; no separate new-base full run is claimed.
