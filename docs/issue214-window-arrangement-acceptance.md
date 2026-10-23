# Issue #214 — Window menu half-screen arrangement

The Window menu offers “将当前窗口放到左半屏” and “将当前窗口放到右半屏”. Focus the menu with Tab, open it with Enter or an arrow key, select an app or placement with Up/Down, and activate with Enter. Selecting an app restores it if minimized. After a selection, focus returns to the Window menu trigger.

Placement leaves 12px around the measured menu/Dock work area and a 12px middle gap. `DesktopShell` observes the menu and the outer Dock nav with native `ResizeObserver`, and remeasures on viewport resize and immediately before an action. The outer Dock box stays in its natural position while auto-hide transforms the inner content, so keyboard revelation of the Dock does not overlap a newly placed window. `halfScreenPlacement` is pure JavaScript; it uses raw bounds and each app's minimum width/height rather than inflating a small work area.

The actions are disabled below 1024px, where compact CSS controls the frame, or when a half cannot fit the current app's minimum. The menu shows the reason in ordinary text; `aria-describedby` also exposes it on the menu and its keyboard focused trigger. No active window, unmeasured chrome and initial desktop load failure are safe disabled states. At 1440px, a 702px half fits Settings (minimum 680px), while Development (minimum 720px) and Knowledge (820px) remain disabled. This is a per-window action; it does not resize another app or promise that every pair of apps fits the viewport.

The result is an ordinary window rectangle saved through the existing layout PUT and autosave queue. Drag, resize, minimize, focus and maximize/restore retain their existing behavior. A maximized window can become a normal half-screen window; maximizing that result and restoring returns to the half-screen rectangle. There is no persistent snap field, backend API change or layout migration. Later free edits or viewport changes retain the existing reachability behavior; windows are not permanently locked to half-screen positions.

## Repeatable local checks

```bash
npm ci --offline --no-audit --no-fund --prefix apps/desktop-web
npm test --prefix apps/desktop-web
npm run build --prefix apps/desktop-web
CHROME_BIN=/usr/bin/google-chrome UI_SCREENSHOT_DIR="$PWD/runtime/verification/issue214" \
  node --test apps/desktop-web/tests/browser-acceptance-window-arrangement.test.mjs
node --test apps/desktop-web/tests/browser-acceptance-shell.test.mjs
uv run pytest
uv run ruff check xeonolab tests scripts
uv run python scripts/export_openapi.py
```

The new test is included in `npm run test:browser-regression`. It uses actual Chrome, repository DesktopShell/components/styles and `shell-fixture` with injected synthetic API functions. It blocks non-local HTTP requests; it uses no production session, identity, server or data. These checks validate client behavior and the synthetic layout contract, not real OIDC/network/backend durability. No new production service dependency is introduced.

The focused browser test covers:

- no active window and initial loadError, with visible and accessible disabled reasons;
- 1920×1080 opposite halves selected entirely through keyboard menus, unchanged frame nodes and working content controls;
- maximize/restore, maximize-to-half, free keyboard move/resize and pointer drag/resize;
- minimize/menu restore, autosave, explicit save and reload with ordinary geometry;
- small/medium/large Dock and both auto-hide states, including keyboard revelation;
- odd viewport widths, differing app minima, compact mode, insufficient height and recovery;
- observer disconnection on React unmount, no uncaught page errors and no non-local requests.

At 1920×1080, the Settings and Development halves have x=12/966 and width=942; y=42. Dock tops are 994/988/980 for small/medium/large, and corresponding window bottoms are 982/976/968. All measured gaps are 12px. At 1921px the right half receives the extra pixel. These figures come from local synthetic browser acceptance, not telemetry.

With `UI_SCREENSHOT_DIR`, the test saves `acceptance.json` and three screenshots: `1920x1080-two-halves.png`, `large-dock-keyboard-revealed.png`, and `1023px-disabled-reason.png`. The first two show two separate frames and uncovered Menu Bar/Dock; the third captures the visible unavailable reason inside the open menu. Screenshot content, identity and Work entries are synthetic.

Repository gates on this change: frontend unit tests **73 passed**, frontend TypeScript/Vite build passed, focused arrangement browser **11 checks / 1 test passed**, existing Shell browser **1 test passed**, scoped Ruff passed and OpenAPI export produced no API diff. The implementation report records the final backend result and any existing broad lint limitations. Rollback is reverting this frontend change; existing stored window rectangles remain compatible.
