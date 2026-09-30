# HF-cache API implementation report

Base: `4c1dc83`; follow-up branch: `luna/hf-cache-pool-metrics`.

## Delivered

- `POST /api/v1/cache/ensure` accepts a commit SHA or mutable ref such as `main`. It resolves the request through HF Hub `repo_info`, keys/persists/downloads by the immutable SHA, and returns both `requested_revision` and `resolved_revision`. Queued jobs pin the resolved SHA before entering the queue, so retries and restart recovery cannot follow a moved tag.
- Production downloads use `huggingface_hub.snapshot_download`, with a dry-run metadata size check before transfer. `download_backend` is injectable for local tests. No Hub requests or model downloads were made during verification.
- The service persists item metadata, file sizes/SHA-256 values, pins, priority, access time, and active usage leases in SQLite. Pin/unpin, explicit eviction, leases, list/detail, storage status, and GC endpoints are scope-protected.
- `POST /api/v1/cache/prefetch` writes a durable job, downloads asynchronously, and exposes job state, phase progress, bounded retry, manual retry after failure, and startup recovery for queued/running jobs. The progress percentage marks phases; it is not a byte-level transfer percentage.
- Plan §38 now has structured dependency-signal ingestion, candidate listing, and accept/reject endpoints. Signals alone never start downloads. Candidate acceptance requires a human or cache-admin identity; Issue-sourced candidates receive no automatic acceptance or download. Automatic signal discovery and source verification are not included.
- Eviction and GC refuse pinned or leased items. The 80/85/90% watermarks stop low-priority requests, collect low-priority items at 85%, and stop downloads above 90%. A `HF_CACHE_MAX_BYTES` quota defaults to 2 TiB and applies to tracked item bytes.
- `create_app` accepts a narrow `storage_usage_provider(path)` seam and an optional test owner validator. `HF_CACHE_POOL_METRICS_FILE` selects the built-in root-fed adapter; no PVE credentials or host-command privileges are added to the HF-cache process.
- The metrics reader requires a root-owned regular file by default, no group/world write bit, no symlink, bounded file size, duplicate-free JSON with exactly `pool`, `total`, `used`, `free`, `observed_at`, expected pool name, consistent integer bytes, and a fresh timezone-aware ISO timestamp. `HF_CACHE_EXPECTED_POOL` defaults to `aux-10t`; `HF_CACHE_POOL_METRICS_MAX_AGE_SECONDS` defaults to 120.
- Pool and guest filesystem are separate constraints: the watermark uses the higher pool/guest utilization percentage; available bytes use `min(pool_free, guest_free, remaining_cache_quota)`. Thus ZFS free space cannot override guest filesystem/quota limits. Stale/invalid telemetry blocks new downloads while item list/detail and already-cached ensure remain readable.
- `docs/api/hf-cache.openapi.json` is regenerated. Account scope catalog now includes `hf-cache:read`, `hf-cache:write`, and `hf-cache:admin`.

## Verification

- `TMPDIR="$PWD/runtime/tmp" uv run --offline pytest -q` — **103 passed**. Cache-specific tests cover tag resolution and cache-key changes, real local file persistence and hashes, corruption rejection, pins/leases/GC, quotas/watermarks, async completion/retry/recovery, candidate acceptance/rejection, mocked official Hub resolver/downloader calls, and root-fed metrics ownership/mode/symlink/schema/pool/freshness plus 80/85/90 decisions and guest-free-space enforcement. No Hub network request or model download was made. One existing Starlette/httpx deprecation warning remains in the project test stack.
- `uv run --offline python scripts/export_openapi.py` — passed.
- `uv run --offline ruff check xeonolab/hf_cache.py xeonolab/services.py xeonolab/accounts.py tests/test_hf_cache.py` — passed.
- `git diff --check` — passed.
- `uv run --offline ruff check` — reports 22 existing infra lint errors in files outside this change (including `infra/systemd/activate.py`, `infra/systemd/backup.py`, and `infra/oidc/init_db.py`).

## Not yet production-complete

- The root-fed reader is implemented, but no PVE collector, atomic file writer, or VM 103 receiver is included. Until root wires the provided metrics file, the default provider measures only the mounted filesystem; physical shared-pool enforcement is not active in production.
- The per-cache quota counts registered snapshot files; administrators must keep the cache root exclusive to this service. Unregistered data in the root is not included in quota accounting.
- Automatic Forgejo/deployment/requirements scanners and evidence verification remain unimplemented; signals are currently submitted through the structured API. Production deployment may want a stricter allowlist for sources eligible for acceptance.
- Progress reports completed phases rather than actual downloaded bytes. Service token provisioning for the new scopes remains operational configuration.

## Suggested follow-up Issue text

**Title:** Add PVE collector and forced receiver for HF-cache pool metrics

**Body:** HF-cache has a root-fed file reader configured with `HF_CACHE_POOL_METRICS_FILE`, expected pool `aux-10t`, and a 120-second freshness default. Implement the PVE-side collector and narrowly scoped fixed receiver that atomically writes `{pool,total,used,free,observed_at}` to VM 103 at least once per minute with root ownership and no group/world write access. The HF-cache service must remain unprivileged and must also enforce its guest filesystem free space; pool free space cannot override the guest quota. Verify production pool identity and report stale/unavailable data as fail-closed for new downloads. Do not grant arbitrary host command execution to the receiver.
