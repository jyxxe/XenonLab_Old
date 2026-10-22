# Issue 180 — iframe registry navigation status

Baseline: `cfae14f2443af80da4647356b29995a32c1dc33d`. Local implementation only; no deployment performed.

`IFrameApp` uses the resolved `appEntryUrl` as navigation identity. An origin or entry change resets connection/error state and starts the existing page-load deadline. Its iframe key includes that URL and explicit retry attempt, replacing only the document being navigated. Registry title, theme, active/minimized state do not change this key. AskAI handoff state remains outside the iframe and stays unsent.

The message handler's exact origin and `contentWindow` checks are unchanged. The existing deadline only diagnoses a page that has not loaded. Once B loads without `app.ready`, it stays “页面已载入；认证和业务连接尚未确认。” even after the deadline. This does not add a ready-handshake timeout or authenticate the external application.

## Reproduce the regression checks

```sh
cd apps/desktop-web
npm ci --offline --no-audit --no-fund
cd ../..
node scripts/verification/iframe-navigation-status/verify.mjs
```

Requires local Google Chrome, Node, uv and the repository's locked dependencies. The runner uses real DesktopShell, Settings, AppRegistry and IFrameApp, actual FastAPI desktop service and temporary SQLite. Its development identity is explicitly synthetic (`round31-synthetic-admin`, desktop:read + desktop:admin); it never reads identity files or production data. Two owned loopback HTML servers emit ordinary ready/status messages. Temporary fixture copies are removed in finally.

Eight groups cover origin A ready → B loaded/no ready with actual API revision 1→2 readback; same-origin entry ready; title-only no reload; old error clearing; auth/network status and explicit retry; minimized/singleton frame-node preservation; theme with unchanged frame plus unsent draft preservation; a pending page request timing out and a subsequent ready navigation recovering. A direct real IFrameApp probe uses a 400ms load deadline; B's loaded/no-ready state is checked after 1.5s. This is a test timeout configuration, not a production change.

Chrome can re-request a document when the existing Shell moves window DOM during foregrounding. The minimize/foreground assertion covers preservation of the same iframe node; it does not claim all existing window movements make zero network requests. The isolated title/theme checks explicitly verify no document requests.

Evidence is written to `runtime/verification/iframe-navigation-status/`: browser-report.json and screenshots of B unconfirmed, ready after retry, and retained AI draft. Eight groups passed with zero page errors. B-unconfirmed screenshot was visually inspected.

## Validation

- `uv run pytest`: 479 passed, 19 warnings (including concurrent temporary pytest-directory cleanup warning).
- `uv run ruff check xeonolab tests scripts/verification/iframe-navigation-status/local_desktop_api.py`: passed.
- `uv run python scripts/export_openapi.py`: passed; no schema diff.
- `npm test`: 62 passed.
- `npm run build`: passed.
- `node --test tests/browser-acceptance-shell.test.mjs`: 1 real Chrome shell acceptance passed (no skip).
- Chrome helper: eight groups passed, zero page errors.
- `git diff --check`: passed.
- IFrameApp source SHA256: `bda32b17295421eac8269148b9807e518b903d2b1a506ae0bf2e0b8174efa84c`.

No new dependencies, migrations, backend protocol or permission changes. Rollback is reverting the IFrameApp navigation lifecycle patch. Real production app origins and authentication remain production acceptance dependencies; local ready/status pages do not prove deployment or external business connectivity.

Coordinator refresh after maintainer branch-sync request: rebased onto merged #178 (`f1b3e16`) and reran all gates/eight Chrome groups,0pageerrors. First backend run reproduced the existing telemetry timing assertion at test_pool_telemetry.py:317; full rerun passed479/19warnings. Both logs retained; no receiver/test change.

Coordinator latest refresh after merged #179: all479pytest/19warnings,62unit,Ruff,unchanged OpenAPI,build and eight local API/Chrome groups passed on `e74301b`,0pageerrors. Verification report had a historical hardcoded37d baseline label; corrected the runtime record and replaced the runner label with actual Git revision/component SHA256 fields. Only report metadata changed; assertions and product source unchanged.


Coordinator refresh after merged #188: rebased onto `cfae14f2443af80da4647356b29995a32c1dc33d`;479 pytest/19warnings first pass,62 frontend tests,Ruff,unchanged OpenAPI,build and eight actual local API/Chrome groups passed,0pageerrors. IFrameApp source hash is unchanged. Browser evidence records actual source revision and component hashes. Local synthetic data only; no production acceptance.
