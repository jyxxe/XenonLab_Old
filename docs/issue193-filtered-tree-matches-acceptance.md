# Issue 193 — Reveal filtered catalogue matches

Baseline: `1aa3d1f7d698c9340b1b38568fd175a974afd012`.

The sidebar's nonempty trimmed filter now renders its retained tree with a temporary expanded-path set. Matched tables and path descendants are immediately visible even when their original ancestors were folded. Explicit node clicks or native Enter/Space toggle only this filter's temporary collapses; rendered nodes and `aria-expanded` use the same effective expansion. Changing the filter resets the temporary collapses. An empty or whitespace-only filter restores the original parent-owned expansion state.

Opening a filtered result uses the existing table-open route with automatic ancestor expansion disabled. This is necessary because the original table-open handler expanded ancestors in the persistent set, which would otherwise change the user's folded state before they cleared the filter. The optional flag defaults to true, preserving all unfiltered and other table-open entries. Search, row selection/Recent, API contracts and catalogue pagination are unchanged.

The pure `filteredTreeExpansion` helper works on the already filtered tree; it never fetches data or mutates its collapse set. Two regression tests exercise deep matches, no matches, path descendants and explicit transient collapse. No new endpoint, scope, dependency or migration.

## Reproduce

```sh
npm ci --offline --no-audit --no-fund --prefix apps/desktop-web
node scripts/verification/filtered-tree-matches/verify.mjs
```

Actual LibraryApp/sidebar and project styles run in Chrome against this worktree's real temporary XeLibrary FastAPI/SQLite service. Setup creates 103 owned synthetic tables through a lawful development human token. The matching identity shape is provided by the fixture because XeLibrary is not the desktop identity service. Catalogue responses, schema reads and pagination are real local backend responses. No production data or identity files are accessed.

Eight browser groups cover deep table-name matching with trim/case handling and no additional requests; Tab/Enter/Space node accessibility with correct aria-expanded; path matches revealing 100 loaded descendants; explicit no-match state; keyboard opening the exact table with successful real schema read then clearing to original folded state; new-filter reset and whitespace clear; an unloaded match remaining absent until explicit catalogue pagination; and ordinary unfiltered fold preferences surviving filter/clear.

Evidence: `runtime/verification/filtered-tree-matches/browser-report.json`, actual source hashes/calls, `deep-filter-match.png`, `clear-after-open-restores-fold.png`, `explicit-next-page-match.png` and gate logs. Screenshots were visually checked: matching button is discoverable, selected real header is correct, original folded root returns, and next-page match appears. Final browser: eight groups passed, zero page errors.

Validation: `uv run pytest` 479 passed (19 warnings); scoped Ruff passed; OpenAPI export passed with no generated schema diff; frontend tests 64 passed (two new tests); frontend build passed; helper syntax and git diff check passed.

Only loaded API-visible tables participate; typing does not fetch more pages or search the server. The existing explicit “加载更多知识表” action remains available. Production deployment, user frequency and a complete remote directory inventory are not verified. Revert the frontend changes to roll back; no data migration or production dependency is required.
