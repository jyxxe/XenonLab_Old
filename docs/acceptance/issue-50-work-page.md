# Issue #50 Work page acceptance fixture

Added `npm run test:work-browser` in `apps/desktop-web`. It launches local Vite and headless Chrome, mounts `Work` with synthetic API responses, and exercises two 100-item cursor pages (including six repeated IDs), refresh during a pending page request, loaded-only filtering, Forgejo link validation, and claim/release/complete affordances. It verifies that a null cursor shows an explicit end state and that a repeated cursor stops pagination with a refresh prompt. The rendered rows expose `data-work-id` so the fixture can assert ID uniqueness directly.

Verification on the merged Issue #50 branch: browser acceptance passed (1); desktop Node tests passed (11); desktop TypeScript/Vite build passed; `uv run pytest -q` passed (189 tests); `uv run python scripts/export_openapi.py` completed with no generated OpenAPI changes; `git diff --check` passed.

No Work page behavior defect surfaced in this local synthetic run. It does not establish authenticated production browser acceptance or verify live Work API data. This branch has not yet been deployed. Production dependencies called out in ADR 0001 (PostgreSQL, Qdrant, and real model workers) remain outside this fixture and are not verified here.
