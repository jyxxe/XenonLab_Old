# Issue 149 — Query Console pagination state

Baseline: `2aabf623aa88f68c0050774ddb03f2773681abfe`.

Removed the independent local OFFSET counter. The page indicator and both navigation actions now use the validated server plan offset. Loading a recent draft clears its old plan through the existing hook; editing, restoring drafts, explicit OFFSET and re-running the same expression all agree with the actual executed query. No API boundary or history persistence change.

Validation completed:
- `uv run pytest`: 429 passed, 19 existing warnings, 14.50s.
- `uv run ruff check xeonolab tests`: passed. New verification Python helper import formatting corrected; scoped check passed after fix.
- `uv run python scripts/export_openapi.py`: passed, no schema diff.
- Frontend `npm ci`, `npm test`: 62 passed; `npm run build`: passed.
- `CHROME_BIN=/usr/bin/google-chrome node --test tests/browser-acceptance-query-drafts.test.mjs` (frontend cwd): 2 passed, 0 skipped. Existing account isolation/storage/load-no-execution and in-flight identity protections retained.
- `node scripts/verification/query-pagination/verify.mjs` (repository cwd): real local FastAPI XeLibrary + real headless Chrome, passed. Eight temporary rows created through local HTTP; synthetic development identity, no production accounts. A first page 1,2; A next 3,4; rerun A 1,2; B next 7,8; load A then run 1,2 and next 3,4; previous 1,2; explicit OFFSET4 5,6 and next OFFSET6 7,8. Indicator matched plan. Evidence `runtime/verification/issue149-query-pagination/recent-pagination-result.json`, screenshot and logs. Temporary API catalogue/browser profile deleted.
- `git diff --check`: passed.

Tracked regression helper under scripts/verification/query-pagination requires existing frontend npm dependencies, uv, and Google Chrome at /usr/bin/google-chrome; invokes local-only service and creates only temporary data. No new application dependency, migration, or production infrastructure. Rollback by reverting this UI/helper/documentation change. Production rollout/acceptance not performed. No inferred deployment success.
