# HF-cache API implementation report

Base: `16aa0d3`; branch: `luna/hf-cache-api`.

## Delivered

- `POST /api/v1/cache/ensure` stores a content manifest and files under the service data root; identical keys reuse an integrity-verified item. Revisions must be 40-character lowercase commit SHAs, so mutable labels such as `main` cannot silently change an item.
- Production downloads use `huggingface_hub.snapshot_download`, with a dry-run metadata size check before transfer. `download_backend` is injectable for local tests. No Hub requests or model downloads were made during verification.
- The service persists item metadata, file sizes/SHA-256 values, pins, priority, access time, and active usage leases in SQLite. Pin/unpin, explicit eviction, leases, list/detail, storage status, and GC endpoints are scope-protected.
- Eviction and GC refuse pinned or leased items. The 80/85/90% watermarks stop low-priority requests, collect low-priority items at 85%, and stop downloads above 90%. A `HF_CACHE_MAX_BYTES` quota defaults to 2 TiB and applies to tracked item bytes.
- `docs/api/hf-cache.openapi.json` is regenerated. Account scope catalog now includes `hf-cache:read`, `hf-cache:write`, and `hf-cache:admin`.

## Verification

- `TMPDIR="$PWD/runtime/tmp" uv run --offline pytest -q` — **84 passed**. This includes six isolated cache tests for immutable revisions, real local file persistence and hashes, cache reuse, corruption rejection, pin and lease protection, eviction/GC, scope denial, watermarks, quota rejection, and staging cleanup. A Starlette/httpx deprecation warning remains in the project test stack.
- `uv run --offline python scripts/export_openapi.py` — passed.
- `uv run --offline ruff check xeonolab/hf_cache.py xeonolab/services.py xeonolab/accounts.py tests/test_hf_cache.py` — passed.
- `uv run --offline ruff check` — reports 22 existing infra lint errors in files outside this change (including `infra/systemd/activate.py`, `infra/systemd/backup.py`, and `infra/oidc/init_db.py`).

## Not yet production-complete

- Disk usage currently comes from `shutil.disk_usage(cache_root)`. This measures the mounted filesystem; it does not measure combined thin-pool / physical-disk occupancy across the fnOS and HF-cache logical volumes. The plan's physical shared-pool watermarks need a trusted host-side metrics provider/adapter before these thresholds can be considered production enforcement.
- The per-cache quota counts registered snapshot files; administrators must keep the cache root exclusive to this service. Unregistered data in the root is not included in quota accounting.
- Plan §38 prediction remains unimplemented: dependency-signal ingestion, candidate generation/listing, and accept/reject workflow. There is no Issue-text-triggered download path.
- The API downloads synchronously in `ensure`; a queued prefetch job/status, retry/resume, and download progress events remain future work. Service token provisioning for the new scopes also remains operational configuration.

## Suggested follow-up Issue text

**Title:** Add trusted physical storage-pool telemetry for HF-cache watermarks

**Body:** HF-cache currently uses `shutil.disk_usage(cache_root)`, which reports the mounted filesystem and cannot account for total occupancy across the shared thin pool backing fnOS and HF-cache logical volumes. Add a narrowly scoped host-side metrics adapter/API that returns pool identity, total/used/free bytes, validates stale/unavailable data as fail-closed for speculative prefetch, and exposes tests for thresholds at 80/85/90%. Keep the HF-cache service unprivileged and do not grant it arbitrary host command execution. Document how production maps the reported pool to the actual 10-TB backing device.
