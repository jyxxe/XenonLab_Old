# Issue #201 acceptance: System storage shows the HF-cache shared pool watermark

Baseline: `2c3481d96f8ba602be5418a44882ba0aa25187db`; final integration onto release base `0bca9c9c674c168851ac8bf7934b3ec09262e5c5` on 2026-10-22. Branch: `zcode/issue-201-storage-watermark`.

The desktop 「系统」→「存储」tab now reads the existing HF-cache storage API through the user's own bearer identity and renders the verified shared-pool state: pool name and watermark badge (`normal` / `evict_low_priority` / `stop_prefetch` / `retain_protected`), pool usage versus the HF-cache guest filesystem usage as two clearly separated views of the same shared pool with a "不能相加" caution, HF-cache cached bytes, and the verified sampling time. A missing `hf-cache:read` scope shows a permission explanation without numbers. Upstream telemetry that is missing, stale or unreachable (503 `STORAGE_TELEMETRY_UNAVAILABLE`) shows an explicit "缓存池水位暂不可用" state and never renders cached numbers.

Backend: `read_pool_metrics` now propagates the validated `observed_at` from the root-fed snapshot instead of discarding it; `StorageUsage` carries it (optional, so injected test providers and the development fallback stay honest with `sampled_at: null`), and `GET /api/v1/cache/storage` returns it as `sampled_at`. The response no longer contains the server-local cache `path`, so browsers never learn server paths. A non-string `observed_at` from a provider fails closed with 503. All byte totals, watermark levels and freshness checks are unchanged.

## Validation

- Original head gates: `uv run pytest` 482 passed; frontend 64 passed; Ruff, OpenAPI export, build and Chrome storage acceptance passed.
- Final integration gates on base `0bca9c9` (2026-10-22): `uv run pytest` **520 passed** (the 47 `tests/test_hf_cache.py` cases including sampling-time propagation, `sampled_at` null for timestamp-less providers, non-string timestamp fails closed, root-fed schema `observed_at`, `sampled_at`/`path` absence); `uv run ruff check xeonolab tests scripts` passed; `export_openapi.py` produced no API diff; frontend `tsc --noEmit` and **110 unit tests** passed; production build passed; Chrome storage acceptance passed all four cases (`ok`, `forbidden`, `unavailable`, `not-wired`) with zero page errors.
- `uv run ruff check xeonolab tests`: passed.
- `uv run python scripts/export_openapi.py`: passed; generated schemas unchanged (the storage endpoint's response schema is intentionally untyped, pre-existing).
- Frontend `npm test`: 64 passed, zero skipped.
- Frontend `npm run build`: TypeScript and Vite passed.
- `CHROME_BIN=/usr/bin/google-chrome node --test tests/browser-acceptance-issue201-storage.test.mjs`: one passed (success, missing-scope and telemetry-unavailable fixtures; asserts pool/guest usage, watermark badge, percent, cached bytes, real sampled time, no server paths, no numbers in restricted states).
- `node --test tests/browser-acceptance-shell.test.mjs tests/browser-acceptance-session.test.mjs`: passed; the shell regression's storage-tab expectation now asserts the scope notice for an identity without `hf-cache:read`.
- Evidence under `runtime/verification/issue201-storage/` (`browser-report.json`, `system-storage-watermark.png`). All data is a local synthetic fixture; no production account, HF-cache service or real telemetry was accessed.

## Remaining production wiring (not in this change)

The same-origin desktop route cannot be completed today because the HF-cache service has no production node/port assignment yet (`scripts/prepare_deployment.py` has no `hf-cache` entry; VM 103 has no HF-cache service unit — tracked by #66). Once the service address exists, one narrow route block in `infra/network/xeonolab.caddy` (and the public edge equivalent, if any) completes the path, e.g.:

```
@cache_api path /api/v1/cache/*
handle @cache_api {
  reverse_proxy <hf-cache-node>:<port>
}
```

Until then the desktop honestly renders the unavailable state. No deployment was performed by this change; rollback is reverting the UI/API/test/document changes.

## 2026-10-22 final integration onto release base `0bca9c9`

The branch was merged with the current release base; the only conflict was the browser-regression test list, resolved as a union that keeps both the new `browser-acceptance-issue201-storage.test.mjs` and the base's later browser tests. Two integration adaptations were needed and are recorded here honestly:

1. **Fixture follows the current shell contract**: the current `AppViewProps` now includes `apps`, `apiRaw`, `dockIds`, `preferences`, `notify`, `windowId` and related fields (the original fixture predates them), so the isolated `tests/system-fixture.tsx` passes inert synthetic stubs. Product code is unchanged by this adaptation.
2. **Not-yet-wired route renders the honest unavailable state**: the HF-cache service still has no production node/port (#66), so the desktop origin's `/api/v1/cache/storage` currently falls through the API catch-all and answers 404. The storage tab now treats 404/502/503 proxy outcomes like the existing 503 `STORAGE_TELEMETRY_UNAVAILABLE` case: it shows "缓存池水位暂不可用" and never renders numbers, instead of a raw error. The Chrome acceptance gained a `not-wired` case asserting this. When #66 wires the service, the documented Caddy route below makes the real state visible with no further desktop change.

The trusted protected preflight and current-head CI for the exact integration head remain the merge gate; the local Chrome run is coordinator evidence (synthetic fixture only, no production HF-cache API or account).
