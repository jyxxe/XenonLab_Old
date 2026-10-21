# Issue #150: saved desktop widget order

Baseline `2aabf623aa88f68c0050774ddb03f2773681abfe`; branch `codex/general-issue150-widget-order-20261003`.

Settings now shows the enabled widgets in their saved order with labelled, keyboard-accessible up/down buttons and disabled first/last boundaries. DesktopShell renders each existing widget by mapping the ordered preference array; stable widget keys retain component state during moves. Existing enable/disable controls continue removing disabled entries and appending re-enabled entries. The default clock/status order remains when widgets is absent. Existing explicit arrays now determine display order as specified by the Issue; there is no migration.

Reuses the existing preference PATCH queue and saving/error feedback. No backend API/schema, account authority, permission or polling changes. WorkWidget itself is unchanged, so PR #165's Issue links are retained on integration. Wallpaper debounce #146 and Library/Inspector PRs are independent.

## Validation

- `uv run pytest`: 429 passed, 19 existing warnings, 13.90 seconds.
- `uv run ruff check xeonolab tests`: passed.
- `uv run python scripts/export_openapi.py`: passed; schema unchanged.
- Frontend `npm test`: 64 passed, zero skipped, including immutable move and boundary regressions.
- Frontend `npm run build`: TypeScript/Vite passed.
- `CHROME_BIN=/usr/bin/google-chrome node --test apps/desktop-web/tests/browser-acceptance-widget-order.test.mjs`: passed, zero skipped.
- `git diff --check`: passed.

Real local Chrome rendered actual DesktopShell/Settings using synthetic shell-fixture preference API and sessionStorage. Checked missing-widgets default, explicit prior array, keyboard Enter move, first/last disabled buttons, exact ordered PATCH payload, both move directions, disabling and appending on re-enable, refresh persistence, 390×844 order and first search card visibility (x=10px, width=224px), and independent default session. No page errors. Browser case is included in the regression runner. Desktop and mobile screenshots were visually inspected. Evidence and logs: `runtime/verification/issue150-widget-order/`.

The fixture does not verify production login, account storage, actual user frequency or real production network latency. Its search card reports unavailable according to synthetic fixture capabilities; this is unchanged and does not affect sorting validation. No new dependency, migration or production worker. Production deployment has not been performed. Rollback: revert UI/helper/tests/document changes; persisted arrays remain valid under the previous fixed-order renderer.
