# HF-cache API implementation report

Base: `16aa0d3`; follow-up branch: `luna/hf-cache-async-predictive` (parent implementation `98d2f34`).

## Delivered

- `POST /api/v1/cache/ensure` accepts a commit SHA or mutable ref such as `main`. It resolves the request through HF Hub `repo_info`, keys/persists/downloads by the immutable SHA, and returns both `requested_revision` and `resolved_revision`. Queued jobs pin the resolved SHA before entering the queue, so retries and restart recovery cannot follow a moved tag.
- Production downloads use `huggingface_hub.snapshot_download`, with a dry-run metadata size check before transfer. `download_backend` is injectable for local tests. No Hub requests or model downloads were made during verification.
- The service persists item metadata, file sizes/SHA-256 values, pins, priority, access time, and active usage leases in SQLite. Pin/unpin, explicit eviction, leases, list/detail, storage status, and GC endpoints are scope-protected.
- `POST /api/v1/cache/prefetch` writes a durable job, downloads asynchronously, and exposes job state, phase progress, bounded retry, manual retry after failure, and startup recovery for queued/running jobs. The progress percentage marks phases; it is not a byte-level transfer percentage.
- Plan §38 now has structured dependency-signal ingestion, candidate listing, and accept/reject endpoints. Signals alone never start downloads. Candidate acceptance requires a human or cache-admin identity; Issue-sourced candidates receive no automatic acceptance or download. Automatic signal discovery and source verification are not included.
- Eviction and GC refuse pinned or leased items. The 80/85/90% watermarks stop low-priority requests, collect low-priority items at 85%, and stop downloads above 90%. A `HF_CACHE_MAX_BYTES` quota defaults to 2 TiB and applies to tracked item bytes.
- `create_app` accepts a narrow `storage_usage_provider(path)` seam. It validates total/used/free values and fails closed when telemetry is unavailable, allowing a trusted pool-metrics adapter to be injected without giving the service host-command privileges. The default provider remains filesystem-level `shutil.disk_usage`.
- `docs/api/hf-cache.openapi.json` is regenerated. Account scope catalog now includes `hf-cache:read`, `hf-cache:write`, and `hf-cache:admin`.

## Verification

- `TMPDIR="$PWD/runtime/tmp" uv run --offline pytest -q` — **93 passed**. Cache-specific tests cover tag resolution and cache-key changes, real local file persistence and hashes, corruption rejection, pins/leases/GC, quotas/watermarks, scope denial, async completion/retry/recovery, candidate acceptance/rejection, mocked official Hub resolver/downloader calls including dry-run quota refusal, and fail-closed storage telemetry. No Hub network request or model download was made. One existing Starlette/httpx deprecation warning remains in the project test stack.
- `uv run --offline python scripts/export_openapi.py` — passed.
- `uv run --offline ruff check xeonolab/hf_cache.py xeonolab/services.py xeonolab/accounts.py tests/test_hf_cache.py` — passed.
- `git diff --check` — passed.
- `uv run --offline ruff check` — reports 22 existing infra lint errors in files outside this change (including `infra/systemd/activate.py`, `infra/systemd/backup.py`, and `infra/oidc/init_db.py`).

## Not yet production-complete

- The default disk provider measures the mounted filesystem, not combined thin-pool / physical-disk occupancy across fnOS and HF-cache logical volumes. Physical shared-pool enforcement requires wiring the injected provider to a trusted host metrics API and validating pool identity/freshness in production.
- The per-cache quota counts registered snapshot files; administrators must keep the cache root exclusive to this service. Unregistered data in the root is not included in quota accounting.
- Automatic Forgejo/deployment/requirements scanners and evidence verification remain unimplemented; signals are currently submitted through the structured API. Production deployment may want a stricter allowlist for sources eligible for acceptance.
- Progress reports completed phases rather than actual downloaded bytes. Service token provisioning for the new scopes remains operational configuration.

## Suggested follow-up Issue text

**Title:** Add trusted physical storage-pool telemetry for HF-cache watermarks

**Body:** HF-cache now accepts an injected `storage_usage_provider(path)` but defaults to `shutil.disk_usage(cache_root)`, which reports the mounted filesystem and cannot account for total occupancy across the shared thin pool backing fnOS and HF-cache logical volumes. Add a narrowly scoped host-side metrics adapter/API that returns pool identity, total/used/free bytes, validates stale/unavailable data as fail-closed for speculative prefetch, and exposes tests for thresholds at 80/85/90%. Keep the HF-cache service unprivileged and do not grant it arbitrary host command execution. Document how production maps the reported pool to the actual 10-TB backing device.
