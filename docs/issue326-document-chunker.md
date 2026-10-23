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
metadata; persistent cross-service lifecycle remains #305 follow-up. Third-party
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
