# Issue #133: maximized desktop windows

The maximize control now fills the viewport immediately below the 30px menu bar,
including the left, right and bottom edges. Window resize and saved maximized layout
restoration use the same geometry. Restoring retains the previous normal rectangle.

The foreground maximized window temporarily enables the existing Dock autohide
mechanism. Bottom-edge pointer reveal and keyboard focus remain available. Bringing
a normal window forward, minimizing, or restoring the maximized window restores the
user's configured Dock behavior without writing a different preference. Compact
maximized windows override normal widget/margin layout and hide overlapping widgets.

Local verification used Chromium `/usr/bin/google-chrome` and synthetic API fixtures:

- `uv run pytest`: 421 passed, 19 warnings about shared temporary-directory cleanup.
- `npm test` in `apps/desktop-web`: 50 passed.
- `npm run build`: TypeScript and Vite production build passed.
- `UI_SCREENSHOT_DIR=/tmp/xeonolab-general-team-20261002/issue133-screenshots node --test tests/browser-acceptance-shell.test.mjs`: passed, 1 test, no skips; checks native and OpenWebUI iframe windows, desktop/tablet/phone resize, all three Dock sizes, bottom-content hit testing, real pointer Dock reveal, foreground switching, keyboard focus and existing autohide behavior.
- `uv run ruff check xeonolab tests scripts/export_openapi.py`: passed.
- `uv run python scripts/export_openapi.py`: passed, no generated changes.
- `git diff --check`: passed.

The full-repository `uv run ruff check` reports 11 existing findings in
`infra/network/backend-firewall.py`, `infra/oidc/init_db.py`, and
`infra/systemd/{backup,executor,receiver}.py`. These files are unchanged from baseline
`486662f` (`git diff 486662f -- infra` is empty).

Screenshot `issue133-maximized.png` in the above screenshot directory was inspected:
the menu bar remains reachable, the frame fills the remaining viewport, and the Dock
is hidden. This is local browser acceptance, not production validation. No API or
database migration, new dependency, deployment, or production data write is included.
