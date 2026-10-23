# Issue 171: theorem review-status search

Baseline: `2aabf623aa88f68c0050774ddb03f2773681abfe`.

The keyword search now offers an accessible native review-status selector: all, draft, proposed, promoted and rejected. Changing it with a nonempty keyword searches that keyword immediately; an empty keyword does not issue a search. The request sends `review_status` only for a specific status. Review-status badges remain separate from formal-status badges.

Clear resets both controls and returns to the existing default list. Refresh also resets search controls. Retry preserves the current keyword and review status. Existing request-generation fencing prevents a delayed search response from replacing a newer status or a cleared list. A successful response with `meta.partial === true` explicitly states that only partial matches are displayed and that this is not the complete review queue. Existing pagination and the 50-result search limit are unchanged.

## Verification

Run from the repository root:

```sh
uv run pytest
uv run ruff check xeonolab tests scripts/verification/theorem-review-filter
uv run python scripts/export_openapi.py
node scripts/verification/theorem-review-filter/verify.mjs
```

Run `npm test` and `npm run build` in `apps/desktop-web` after installing its locked dependencies.

Results: 429 Python tests passed (19 existing warnings); 62 frontend tests passed; scoped Ruff, OpenAPI generation, frontend build and `git diff --check` passed. OpenAPI output is unchanged.

The browser runner renders the actual LibraryApp, TheoremView and hook in headless Chrome against a temporary loopback XeLibrary API. It creates 61 synthetic records with a declared local development identity. Local promote/reject calls only prepare fixture states; they do not imply production actions or worker verification. The runner verifies nonempty keyword gating, all four filters and actual request bodies/results, distinct review/formal badges, real backend partial metadata, status-change re-search, delayed old-response isolation, clear during a pending response, same-filter retry after an explicitly synthetic failure, and the 390px layout. Both delayed-response checks assert started/unsettled barriers before releasing responses. Nine checks passed with zero page errors.

Evidence is written to `runtime/verification/issue171-theorem-review-filter/`: `browser-report.json`, `theorem-partial-results.png`, `theorem-proposed-filter.png`, `theorem-filter-mobile.png`, and gate logs. Screenshots were visually inspected. This tracked runner is a manual acceptance command, not an added CI job.

## Boundaries

No backend route, permission, formal verification, promotion workflow, schema, migration or dependency changes. No production deployment or production acceptance was performed. Existing LibraryApp changes in pending PRs are independent hunks and must be retained during integration. Rollback consists of reverting the three frontend changes and their verification/documentation artifacts; no data rollback is needed.
