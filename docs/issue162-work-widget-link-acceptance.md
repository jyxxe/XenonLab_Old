# Issue #162 acceptance: WorkWidget Issue links

Baseline: `2aabf623aa88f68c0050774ddb03f2773681abfe`. Branch: `codex/general-issue162-work-widget-link-20261003`.

Each valid active work entry now links its repository, Issue number and title to the existing Forgejo Issue URL in a new tab. The native anchor supports pointer and keyboard activation and uses `noopener noreferrer`. Missing or malformed repository metadata and non-positive/non-integer Issue IDs remain plain text. The task-center URL helper is shared in a dependency-free module, preserving the public `/dev` and LAN base selection without importing the app registry into Widgets. Existing task-center behavior, active-status filtering, five-entry summary and “打开任务” action are preserved.

## Validation

- `uv run pytest`: 429 passed, 19 existing warnings.
- `uv run ruff check xeonolab tests`: passed.
- `uv run python scripts/export_openapi.py`: passed; generated schema unchanged.
- Frontend `npm test`: 64 passed, zero skipped.
- Frontend `npm run build`: TypeScript and Vite passed.
- `CHROME_BIN=/usr/bin/google-chrome node --test apps/desktop-web/tests/browser-acceptance-work-widget-links.test.mjs`: one passed, zero skipped; 1.813 seconds.
- `git diff --check`: passed.

Chrome rendered the actual DesktopShell, WorkWidget and task-center components using the local shell fixture. Pointer click and keyboard Tab/Enter opened the exact same Issue URL in new tabs, with `window.opener === null`. The existing “打开任务” button opened the task center and its Issue link matched. Eight active items retained the five-entry summary. Benign missing/malformed metadata produced plain text; there were no page errors. The regression runner includes this browser case.

Evidence is persisted under `runtime/verification/issue162-work-widget/`: `browser-report.json`, `work-widget-links.png` and command logs. The screenshot was visually inspected for link text, focus outline, status labels and task-center action. Work data and preferences are synthetic fixture sources. The exact external popup URLs were intercepted and fulfilled with a local test response; no real Forgejo Issue page or account was accessed. This validates browser navigation behavior, not production login or deployment.

## Operational scope

No backend API, schema, permission, polling interval, production worker, dependency or migration changes. No deployment was performed. Production navigation still requires the existing Forgejo service and account access. Roll back by reverting these UI/helper/test/document changes. This change is independent of the pending Library and Inspector PRs.
