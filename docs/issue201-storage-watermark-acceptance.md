# Issue #201 acceptance: System storage shows the HF-cache shared pool watermark

Baseline: `2c3481d96f8ba602be5418a44882ba0aa25187db`. Branch: `zcode/issue-201-storage-watermark`.

The desktop 「系统」→「存储」tab now reads the existing HF-cache storage API through the user's own bearer identity and renders the verified shared-pool state: pool name and watermark badge (`normal` / `evict_low_priority` / `stop_prefetch` / `retain_protected`), pool usage versus the HF-cache guest filesystem usage as two clearly separated views of the same shared pool with a "不能相加" caution, HF-cache cached bytes, and the verified sampling time. A missing `hf-cache:read` scope shows a permission explanation without numbers. Upstream telemetry that is missing, stale or unreachable (503 `STORAGE_TELEMETRY_UNAVAILABLE`) shows an explicit "缓存池水位暂不可用" state and never renders cached numbers.

Backend: `read_pool_metrics` now propagates the validated `observed_at` from the root-fed snapshot instead of discarding it; `StorageUsage` carries it (optional, so injected test providers and the development fallback stay honest with `sampled_at: null`), and `GET /api/v1/cache/storage` returns it as `sampled_at`. The response no longer contains the server-local cache `path`, so browsers never learn server paths. A non-string `observed_at` from a provider fails closed with 503. All byte totals, watermark levels and freshness checks are unchanged.

## Validation

- `uv run pytest`: 482 passed (44 → 47 in `tests/test_hf_cache.py`: verified sampling-time propagation, `sampled_at` null for timestamp-less providers, non-string timestamp fails closed; root-fed schema test now covers `observed_at`; integrated storage assertions cover `sampled_at` and `path` absence).
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
