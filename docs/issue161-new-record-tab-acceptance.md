# Issue #161: new-record Inspector tab acceptance

Baseline `2aabf623aa88f68c0050774ddb03f2773681abfe`; branch `codex/general-issue161-new-record-tab-20261003`.

LibraryApp already selected `fields` when the user pressed “新增记录”, but Inspector owned a separate tab state and never received that selection. Inspector now accepts optional controlled tab/onTabChange props, and LibraryApp connects its existing state. Independent Inspector callers retain local tab state by default. The change is limited to these props and the existing tab state; mutation effects and search navigation are unchanged. PR #157 and #160 touch the same files but are not implementation dependencies: their context fences and search behavior must be retained during integration.

## Verification

- `uv run pytest`: 429 passed, 19 existing warnings, 13.98 seconds.
- `uv run ruff check xeonolab tests scripts/verification/new-record-tab`: passed.
- `uv run python scripts/export_openapi.py`: passed, schema unchanged.
- Frontend `npm test`: 62 passed, zero skipped.
- Frontend `npm run build`: TypeScript/Vite passed.
- `node scripts/verification/new-record-tab/verify.mjs`: PASS, seven browser/API checks and zero page errors.
- `git diff --check`: passed.

The reproducible browser runner starts a temporary local FastAPI XeLibrary, creates a table and initial row through its real API, and renders the real LibraryApp and Inspector in Chrome. Synthetic local identity and development token are test-only. Checks cover overview, attachments and history → new record → selected fields tab and visible form; hidden Inspector; selected existing row; two consecutive creations; and normal record overview/history navigation. API reads confirm both created records retain numeric int64 default 17 and boolean default true. The runner waits for the blank new-row draft before screenshotting. No production API or worker was contacted. The standalone Inspector fallback is preserved by the optional-prop contract; the live browser checks exercise the LibraryApp-controlled path.

Evidence: `runtime/verification/issue161-new-record-tab/browser-report.json`, `new-record-fields.png` and gate logs. Screenshot visually inspected for the new-record title, selected fields tab, inputs and creation button.

## Operations

No new dependency, migration, backend API/schema or permission changes. Existing indexing-worker availability is unchanged; successful row creation is not a claim that indexing is running. Production deployment has not been performed. Rollback is a revert of the UI props/state wiring and test/document files. Production session/deployment acceptance remains with the coordinator.
