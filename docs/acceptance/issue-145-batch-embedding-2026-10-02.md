# Issue #145: manual batch Embedding

The app accepts 1–32 separate inputs, each with the existing 32,768-character
limit. Every item must be nonblank. Users can add/remove items; the last item
cannot be removed. A single input still submits a string. Multiple inputs submit
one ordered string array through the existing `/embed/text` API, never separate
requests or background jobs.

Results validate the entire response before publishing: every input requires one
unique integer index in range and a nonempty finite numeric vector. Results are
ordered by index and show the corresponding original input. Missing/duplicate
indexes and malformed vectors produce explicit errors with no partial display.
Draft/request versions from #142 invalidate completed or pending results on input,
model, add or remove edits. Synchronous worker 429 remains an error; the current
request is not cancelled and generating remains busy until it settles.

Local verification on shared #142 head `43c0aae` (including feature baseline `2aabf62`):

- `uv run pytest`: 429 passed, 19 existing test warnings (TestClient deprecation and shared temporary-directory cleanup).
- `cd apps/desktop-web && npm test`: 65 passed; 3 new input/index/vector tests.
- `cd apps/desktop-web && npm run build`: TypeScript/Vite production build passed.
- `uv run ruff check xeonolab tests scripts/export_openapi.py`: passed.
- `uv run python scripts/export_openapi.py`: passed, OpenAPI unchanged.
- `git diff --check`: passed.
- `UI_SCREENSHOT_DIR=<worktree>/runtime/verification/issue145-batch node --test tests/browser-acceptance-shell.test.mjs` from `apps/desktop-web`: 1 passed, 0 skipped, 23.76s; real Google Chrome/React/Vite with local synthetic APIs. Checks two inputs in one request, reverse-order response mapping, per-item vector viewing, blank-item prevention, 32-item limit, removal back to single mode, 429, malformed batch errors, no write scope and all existing #142 stale-response cases.

Evidence `runtime/verification/issue145-batch/issue145-batch-results.png` was
inspected: the two original texts are visibly paired with distinct vectors.
`issue145-batch-report.json` records the actual synthetic request and rendered
results. Synthetic 3-dimensional responses do not demonstrate D100M/Qwen readiness,
real inference accuracy, production queue behavior or production API availability.

No API contract, migration, new production dependency or production write.
Existing model/OIDC dependencies remain as before. Rollback: revert the frontend
PR and rebuild its artifact. No production activation is claimed.

Submission base update: safely fast-forwarded from `a2d62a0` to `43c0aae`; the only base additions were the firewall template and runbook. All listed gates were rerun on the new base, with unchanged frontend build hashes. The intended PR is a draft against the #142 branch until #144 merges.
