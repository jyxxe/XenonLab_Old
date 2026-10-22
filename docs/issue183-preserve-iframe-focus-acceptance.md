# Issue 183 — retain iframe documents when focusing windows

Baseline `1aa3d1f7d698c9340b1b38568fd175a974afd012`. Local implementation; no deployment performed.

`focusWindow` keeps the existing logical stacking array, active-window selection, cycling and persisted layout order. Shell rendering now uses `stableWindowOrder`, a copy sorted by immutable window ID. Each window's z-index still comes from its position in the logical stack. React no longer physically moves existing window nodes merely to bring a window forward, avoiding Chrome's reload of moved iframe documents. Adding or removing a window preserves the relative DOM order of existing windows.

No IFrameApp/message/origin/source/sandbox rules are changed. Actual navigation and explicit reload deliberately replace documents; minimizing keeps a mounted document. No dependencies or migration added. Rollback is reverting the Shell rendering/helper patch.

## Reproduce

```sh
cd apps/desktop-web
npm ci --offline --no-audit --no-fund
cd ../..
node scripts/verification/window-focus-preservation/verify.mjs
```

Chrome, Node, uv and locked frontend dependencies are required. The runner uses this worktree's actual Shell, Settings, AppRegistry, WindowFrame and IFrameApp plus actual local Desktop FastAPI, with cwd/PYTHONPATH fixed to this tree and temporary SQLite. The synthetic development human has desktop:read, desktop:admin and xelibrary:read; no production identity/data are used.

The controlled ordinary external page contains an unsaved input, per-document instance number and normal app.ready. Server counts actual document requests. A registry app is created through Settings and the actual API. Two successive Settings↔editor cycles retain both exact input strings, instance1, one document request and the same frame. Assertions verify z-index and actual center hit-testing belong to the foreground editor. Minimizing/restoring and singleton LaunchPad reopening retain input and document. Real Shell `openApp` AskAI intent also preserves frame input and unsent handoff through subsequent focus switches.

AskAI boundary: only the test native Knowledge component is replaced by a button calling its ordinary `openApp` prop with a synthetic prompt. Shell and IFrameApp handoff are actual components; this is not a claim of end-to-end real XeLibrary selection testing. The fixture does not automatically send any prompt to the external page.

Explicit reload increments requests and resets the ordinary external input. Same-origin entry change and different-origin registry change are saved/read back through the real API and navigate correctly. Seven groups pass with zero page errors. Final screenshots show retained input and document instance1; the after-second-focus screenshot was visually inspected.

## Compatibility with pending #180

```sh
IFRAME_COMPAT_SOURCE=/absolute/path/to/issue180/worktree/apps/desktop-web/src/shell/IFrameApp.tsx node scripts/verification/window-focus-preservation/verify.mjs
```

A test-only Vite load hook reads the approved #180 component at the supplied local path, resolving its relative dependencies against this tree. It does not modify either worktree. The same seven groups pass; additionally the new-origin loaded/no-ready page remains unconfirmed under #180. This is runtime component-combination evidence, not a Git merge, build of a merged branch or deployment. Reports include exact source hashes and override path; no old source is silently represented as current.

## Actual gates and evidence

- `uv run pytest`: initial478 passed/1 failed; unchanged telemetry slow-client test at `tests/test_pool_telemetry.py:317` expected408 but read empty. Targeted rerun passed1; full rerun479 passed,19warnings. All logs retained.
- `npm test`:63 passed, including meaningful stable-DOM-order/nonmutating-stack regression.
- `npm run build`:passed.
- `uv run ruff check xeonolab tests scripts/verification/window-focus-preservation/local_desktop_api.py`:passed.
- `uv run python scripts/export_openapi.py`:passed,no schema diff.
- Real Chrome:seven groups on current source and seven with #180 component; zero page errors in both.
- `git diff --check`:passed.

Evidence: `runtime/verification/window-focus-preservation/` and `runtime/verification/window-focus-preservation-compat180/`; runner removes temporary fixture copies/SQLite in finally. Original coordinator reproduction remains at root runtime/verification/window-focus-parent-current: same DOM node/URL with document requests1→2→3 and lost input. The fix checks actual document/input/request state, not just iframe-node identity.

Source hashes: DesktopShell `a9938f06e0a4f8491eb09ca0af5bda01bcf51120c98e45c7b500c23fd3938cd3`; window-manager `3e9904f9bc5cbb47c8a784a34d772eed33165430030431615426d0b66bd3462d`.

Production external applications and authentication remain unverified; local Chrome/ordinary pages do not prove a release or external business connection. The initial unrelated test failure is reported, not hidden. No remote writes, commits, pushes or deployment were performed by this agent.

Coordinator independently reran all gates on the frozen source:479 pytest passed/19warnings on first parent run,63 frontend unit tests,build,Ruff,unchanged OpenAPI,both seven-group browser suites and the complete existing Shell browser acceptance (1 passed,no skips) passed. Final retained-draft screenshot visually inspected. Child first telemetry failure remains recorded above.


Latest coordinator acceptance on base `e74301b03fab2cde4e50e395c02bc041862cde85`: 479 pytest/19 warnings on first parent run,63 frontend tests,Ruff,unchanged OpenAPI,build,7 current-source and7 readonly #180 component-override Chrome checks,0 page errors;complete existing Shell acceptance1 passed,no skips. Synthetic local data only; no production acceptance.


Latest coordinator acceptance on base `cfae14f2443af80da4647356b29995a32c1dc33d`: 479pytest19warnings firstpass63frontend/Ruff/OpenAPIunchanged/build/7+7actualAPIChrome0errors/fullShell1passnoskip. The product source is unchanged from the frozen implementation. Synthetic local services/data only; no deployment or production acceptance.


Coordinator follow-up on base `1aa3d1f7d698c9340b1b38568fd175a974afd012`: 479pytest19warnings firstpass,63frontend,build,Ruff,unchanged OpenAPI,7 current +7 readonly #180 component-override Chrome groups and existing complete Shell browser test1passed/noskips. Source hashes for DesktopShell/window-manager and readonly #180 remain unchanged. Fresh response to repeated HTTP405 removes the same observable old merge-base precondition called out on #189; repository branch-protection GET was denied403 to the General account, so no server configuration or definitive405 cause is claimed. No own merge/deployment.
