"""Revision-pinned Hugging Face snapshot cache with durable manifests and leases."""

import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, response, scope, transaction

REVISION = re.compile(r"^[0-9a-f]{40}$")
_ENSURE_LOCK = threading.Lock()
_PREFETCH_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="hf-cache-prefetch")


@dataclass(frozen=True)
class StorageUsage:
    pool: str | None
    pool_total: int
    pool_used: int
    pool_free: int
    guest_total: int
    guest_used: int
    guest_free: int

    @property
    def pressure_percent(self):
        return max(self.pool_used * 100 / self.pool_total, self.guest_used * 100 / self.guest_total)

    @property
    def available_bytes(self):
        return min(self.pool_free, self.guest_free)


def read_pool_metrics(path, *, expected_pool="aux-10t", owner_validator=None, now=None, max_age_seconds=120):
    """Read one root-fed atomic snapshot; never follow a symlink or trust stale data."""
    path = Path(path)
    if not path.is_absolute():
        raise APIError(503, "STORAGE_TELEMETRY_UNAVAILABLE", "metrics path must be absolute")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError("metrics path must be a regular non-symlink file")
        fd = os.open(path, flags)
        try:
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino) or not stat.S_ISREG(info.st_mode):
                raise ValueError("metrics file changed while opening")
            if info.st_mode & 0o022:
                raise ValueError("metrics file must not be group/world writable")
            valid_owner = owner_validator(info) if owner_validator else info.st_uid == 0
            if not valid_owner:
                raise ValueError("metrics file must be root-owned")
            with os.fdopen(fd, "rb", closefd=False) as stream:
                raw = stream.read(16385)
            if len(raw) > 16384:
                raise ValueError("metrics file exceeds size limit")
        finally:
            os.close(fd)

        def unique_object(pairs):
            data = {}
            for key, value in pairs:
                if key in data:
                    raise ValueError("duplicate JSON keys")
                data[key] = value
            return data

        data = json.loads(raw, object_pairs_hook=unique_object)
        if set(data) != {"pool", "total", "used", "free", "observed_at"}:
            raise ValueError("unexpected metrics schema")
        if data["pool"] != expected_pool:
            raise ValueError("unexpected storage pool")
        total, used, free = data["total"], data["used"], data["free"]
        if any(type(value) is not int for value in (total, used, free)):
            raise ValueError("storage byte values must be integers")
        if total <= 0 or used < 0 or free < 0 or used + free != total:
            raise ValueError("inconsistent storage byte values")
        if not isinstance(data["observed_at"], str):
            raise ValueError("observed_at must be an ISO timestamp")
        observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        now = now or datetime.now(timezone.utc)
        age = (now - observed.astimezone(timezone.utc)).total_seconds()
        if age < -30 or age > max_age_seconds:
            raise ValueError("storage metrics are stale or from the future")
        return {"pool": expected_pool, "total": total, "used": used, "free": free}
    except APIError:
        raise
    except Exception:
        raise APIError(
            503, "STORAGE_TELEMETRY_UNAVAILABLE", "trusted storage telemetry unavailable"
        ) from None


class Ensure(BaseModel):
    repo_type: str = Field(default="model", pattern="^(model|dataset|space)$")
    repo_id: str = Field(min_length=3, max_length=256, pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    revision: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._/-]+$")
    allow_patterns: list[str] = Field(default_factory=list, max_length=128)
    ignore_patterns: list[str] = Field(default_factory=list, max_length=128)
    priority: str = Field(default="normal", pattern="^(low|normal|production)$")
    pin: bool = False


class GCRequest(BaseModel):
    target_usage_percent: float = Field(default=75, gt=0, le=100)


class DependencySignal(BaseModel):
    source: str = Field(pattern="^(forgejo|deployment_manifest|requirements|config|issue|task_queue)$")
    repository: str = Field(min_length=1, max_length=256)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    repo_type: str = Field(default="model", pattern="^(model|dataset|space)$")
    repo_id: str = Field(min_length=3, max_length=256, pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    revision: str = Field(default="main", min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._/-]+$")
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1, max_length=2048)


def _canonical(body):
    return json.dumps(
        {
            key: getattr(body, key)
            for key in ("repo_type", "repo_id", "revision", "allow_patterns", "ignore_patterns")
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _hub_download(repo_type, repo_id, revision, destination, allow_patterns, ignore_patterns, max_bytes):
    # Hugging Face Hub is the production downloader; tests inject a local backend.
    from huggingface_hub import snapshot_download

    estimate = snapshot_download(
        repo_id=repo_id,
        repo_type=repo_type,
        revision=revision,
        allow_patterns=allow_patterns or None,
        ignore_patterns=ignore_patterns or None,
        dry_run=True,
    )
    sizes = [getattr(entry, "file_size", None) for entry in estimate]
    if any(size is None or size < 0 for size in sizes) or sum(sizes) > max_bytes:
        raise APIError(413, "CACHE_QUOTA_EXCEEDED", "snapshot exceeds available HF-cache quota")
    return snapshot_download(
        repo_id=repo_id,
        repo_type=repo_type,
        revision=revision,
        local_dir=str(destination),
        allow_patterns=allow_patterns or None,
        ignore_patterns=ignore_patterns or None,
    )


def _resolve_hf_revision(repo_type, repo_id, revision):
    from huggingface_hub import HfApi

    try:
        info = HfApi().repo_info(repo_id=repo_id, repo_type=repo_type, revision=revision)
    except Exception as exc:
        raise APIError(
            502, "REVISION_RESOLUTION_FAILED", f"could not resolve Hub revision: {type(exc).__name__}"
        ) from None
    resolved = getattr(info, "sha", None)
    if not isinstance(resolved, str) or not REVISION.fullmatch(resolved):
        raise APIError(502, "INVALID_HUB_REVISION", "Hub did not return an immutable commit SHA")
    return resolved


def _file_manifest(root):
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise APIError(502, "UNSAFE_CACHE_CONTENT", "download contains a symbolic link")
        if path.is_file():
            result.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _digest(path),
                }
            )
    if not result:
        raise APIError(502, "EMPTY_SNAPSHOT", "download produced no files")
    return result


def create_app(
    settings=None,
    *,
    download_backend=None,
    revision_resolver=None,
    storage_usage_provider=None,
    pool_metrics_owner_validator=None,
):
    app = base_app("hf-cache", settings)
    root = app.state.settings.data_dir / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    db_path = app.state.settings.data_dir / "hf-cache.sqlite"
    with transaction(db_path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY, cache_key TEXT UNIQUE NOT NULL, repo_type TEXT NOT NULL,
            repo_id TEXT NOT NULL, revision TEXT NOT NULL, allow_patterns TEXT NOT NULL,
            ignore_patterns TEXT NOT NULL, priority TEXT NOT NULL, path TEXT NOT NULL,
            manifest TEXT NOT NULL, bytes INTEGER NOT NULL, created REAL NOT NULL,
            accessed REAL NOT NULL, pinned INTEGER NOT NULL DEFAULT 0)""")
        db.execute("""CREATE TABLE IF NOT EXISTS leases (
            id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES items(id) ON DELETE CASCADE,
            owner TEXT NOT NULL, created REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS prefetch_jobs (
            id TEXT PRIMARY KEY, payload TEXT NOT NULL, status TEXT NOT NULL,
            progress INTEGER NOT NULL, phase TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 3, item_id TEXT, error TEXT,
            created REAL NOT NULL, updated REAL NOT NULL,
            requested_revision TEXT, resolved_revision TEXT)""")
        db.execute("""CREATE TABLE IF NOT EXISTS dependency_candidates (
            id TEXT PRIMARY KEY, signal TEXT NOT NULL, status TEXT NOT NULL,
            created REAL NOT NULL, updated REAL NOT NULL, job_id TEXT)""")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(items)")}
        if "requested_revision" not in columns:
            db.execute("ALTER TABLE items ADD COLUMN requested_revision TEXT")
            db.execute("UPDATE items SET requested_revision=revision WHERE requested_revision IS NULL")
        job_columns = {row["name"] for row in db.execute("PRAGMA table_info(prefetch_jobs)")}
        if "requested_revision" not in job_columns:
            db.execute("ALTER TABLE prefetch_jobs ADD COLUMN requested_revision TEXT")
        if "resolved_revision" not in job_columns:
            db.execute("ALTER TABLE prefetch_jobs ADD COLUMN resolved_revision TEXT")

    backend = download_backend or _hub_download
    resolve_revision = revision_resolver or _resolve_hf_revision
    metrics_path = os.getenv("HF_CACHE_POOL_METRICS_FILE")
    expected_pool = os.getenv("HF_CACHE_EXPECTED_POOL", "aux-10t")
    try:
        metrics_max_age = int(os.getenv("HF_CACHE_POOL_METRICS_MAX_AGE_SECONDS", "120"))
    except ValueError:
        raise RuntimeError("HF_CACHE_POOL_METRICS_MAX_AGE_SECONDS must be a positive integer") from None
    if metrics_max_age <= 0:
        raise RuntimeError("HF_CACHE_POOL_METRICS_MAX_AGE_SECONDS must be a positive integer")
    if storage_usage_provider:
        usage_provider = storage_usage_provider
    elif metrics_path:

        def usage_provider(_path):
            return read_pool_metrics(
                metrics_path,
                expected_pool=expected_pool,
                owner_validator=pool_metrics_owner_validator,
                max_age_seconds=metrics_max_age,
            )
    else:

        def usage_provider(path):
            return shutil.disk_usage(path)

    try:
        cache_quota = int(os.getenv("HF_CACHE_MAX_BYTES", str(2 * 1024**4)))
    except ValueError:
        raise RuntimeError("HF_CACHE_MAX_BYTES must be a positive integer") from None
    if cache_quota <= 0:
        raise RuntimeError("HF_CACHE_MAX_BYTES must be a positive integer")
    app.state.ready_check = lambda: True

    def storage_usage():
        try:
            pool_usage = usage_provider(root)
            guest_usage = shutil.disk_usage(root)
            if isinstance(pool_usage, dict):
                pool, total, used, free = (
                    pool_usage.get("pool"),
                    pool_usage.get("total"),
                    pool_usage.get("used"),
                    pool_usage.get("free"),
                )
            else:
                pool = getattr(pool_usage, "pool", None)
                total, used, free = pool_usage.total, pool_usage.used, pool_usage.free
            if (
                type(total) is not int
                or type(used) is not int
                or type(free) is not int
                or total <= 0
                or used < 0
                or used > total
                or free < 0
                or guest_usage.total <= 0
                or guest_usage.used < 0
                or guest_usage.free < 0
            ):
                raise ValueError("invalid storage usage values")
            return StorageUsage(
                pool,
                total,
                used,
                free,
                guest_usage.total,
                guest_usage.used,
                guest_usage.free,
            )
        except Exception:
            raise APIError(
                503, "STORAGE_TELEMETRY_UNAVAILABLE", "trusted storage telemetry unavailable"
            ) from None

    def get_item(db, item_id):
        row = db.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise APIError(404, "CACHE_ITEM_NOT_FOUND", "cache item not found")
        return row

    def public_item(db, row):
        live_leases = db.execute("SELECT COUNT(*) FROM leases WHERE item_id=?", (row["id"],)).fetchone()[0]
        return {
            "id": row["id"],
            "repo_type": row["repo_type"],
            "repo_id": row["repo_id"],
            "revision": row["revision"],
            "resolved_revision": row["revision"],
            "requested_revision": row["requested_revision"],
            "priority": row["priority"],
            "pinned": bool(row["pinned"]),
            "in_use": live_leases > 0,
            "lease_count": live_leases,
            "bytes": row["bytes"],
            "files": json.loads(row["manifest"]),
            "created_at": row["created"],
        }

    def item_files_valid(row):
        base = Path(row["path"])
        try:
            for entry in json.loads(row["manifest"]):
                candidate = base / entry["path"]
                if base not in candidate.parents or candidate.is_symlink() or not candidate.is_file():
                    return False
                if candidate.stat().st_size != entry["size"] or _digest(candidate) != entry["sha256"]:
                    return False
            return bool(json.loads(row["manifest"]))
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def collect(target_percent, *, low_only=False):
        usage = storage_usage()
        removed = []
        priority_filter = "AND i.priority='low'" if low_only else ""
        with transaction(db_path) as db:
            rows = db.execute(
                f"""SELECT i.* FROM items i WHERE i.pinned=0 {priority_filter}
                AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.item_id=i.id)
                ORDER BY CASE i.priority WHEN 'low' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, i.accessed ASC"""
            ).fetchall()
            for row in rows:
                if usage.pressure_percent <= target_percent:
                    break
                item_path = Path(row["path"])
                if root not in item_path.parents or item_path.is_symlink():
                    raise APIError(500, "CACHE_PATH_INVALID", "refusing to delete outside cache root")
                shutil.rmtree(item_path)
                db.execute("DELETE FROM items WHERE id=?", (row["id"],))
                removed.append(row["id"])
                usage = storage_usage()
        return removed, usage

    def ensure_item(body, progress=lambda value, phase: None, requested_revision=None):
        requested_revision = requested_revision or body.revision
        progress(5, "resolving_revision")
        resolved_revision = resolve_revision(body.repo_type, body.repo_id, body.revision)
        if not isinstance(resolved_revision, str) or not REVISION.fullmatch(resolved_revision):
            raise APIError(502, "INVALID_HUB_REVISION", "revision resolver did not return a commit SHA")
        resolved_body = body.model_copy(update={"revision": resolved_revision})
        key = _canonical(resolved_body)
        with _ENSURE_LOCK:
            with connect(db_path) as db:
                old = db.execute("SELECT * FROM items WHERE cache_key=?", (key,)).fetchone()
            if old and item_files_valid(old):
                with transaction(db_path) as db:
                    db.execute(
                        "UPDATE items SET accessed=?, priority=?, pinned=MAX(pinned, ?) WHERE id=?",
                        (time.time(), body.priority, int(body.pin), old["id"]),
                    )
                    row = get_item(db, old["id"])
                    result = {
                        "status": "cached",
                        **public_item(db, row),
                        "requested_revision": requested_revision,
                    }
                progress(100, "cached")
                return result
            if old:
                raise APIError(500, "CACHE_INTEGRITY_FAILURE", "cached snapshot failed manifest verification")
            item_id = str(uuid4())
            final = root / item_id
            staging = root / ("." + item_id + ".partial")
            try:
                usage = storage_usage()
                used_percent = usage.pressure_percent
                if used_percent >= 90:
                    raise APIError(
                        507, "DISK_WATERMARK", "cache downloads disabled above the 90% disk watermark"
                    )
                if used_percent >= 80 and body.priority == "low":
                    raise APIError(
                        507, "DISK_WATERMARK", "low-priority cache downloads stop at 80% disk usage"
                    )
                if used_percent >= 85:
                    collect(80, low_only=True)
                    usage = storage_usage()
                    used_percent = usage.pressure_percent
                    if used_percent >= 90:
                        raise APIError(
                            507, "DISK_WATERMARK", "cache downloads disabled above the 90% disk watermark"
                        )
                with connect(db_path) as db:
                    current_bytes = db.execute("SELECT COALESCE(SUM(bytes),0) FROM items").fetchone()[0]
                remaining = min(cache_quota - current_bytes, usage.available_bytes)
                if remaining <= 0:
                    raise APIError(507, "CACHE_QUOTA_EXCEEDED", "HF-cache byte quota is full")
                progress(10, "downloading")
                backend(
                    body.repo_type,
                    body.repo_id,
                    resolved_revision,
                    staging,
                    body.allow_patterns,
                    body.ignore_patterns,
                    remaining,
                )
                progress(90, "verifying_files")
                manifest = _file_manifest(staging)
                total = sum(file["size"] for file in manifest)
                if total > remaining:
                    raise APIError(413, "CACHE_QUOTA_EXCEEDED", "snapshot exceeds remaining HF-cache quota")
                (staging / ".xeonolab-manifest.json").write_text(
                    json.dumps({"cache_key": key, "files": manifest}, sort_keys=True, separators=(",", ":"))
                )
                os.replace(staging, final)
                now = time.time()
                with transaction(db_path) as db:
                    db.execute(
                        "INSERT INTO items (id,cache_key,repo_type,repo_id,revision,allow_patterns,ignore_patterns,priority,path,manifest,bytes,created,accessed,pinned,requested_revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            item_id,
                            key,
                            body.repo_type,
                            body.repo_id,
                            resolved_revision,
                            json.dumps(body.allow_patterns),
                            json.dumps(body.ignore_patterns),
                            body.priority,
                            str(final),
                            json.dumps(manifest, sort_keys=True),
                            total,
                            now,
                            now,
                            int(body.pin),
                            requested_revision,
                        ),
                    )
                    row = get_item(db, item_id)
                    result = {
                        "status": "ready",
                        **public_item(db, row),
                        "requested_revision": requested_revision,
                    }
                progress(100, "ready")
                return result
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                if final.exists():
                    shutil.rmtree(final, ignore_errors=True)
                raise

    @app.post("/api/v1/cache/ensure")
    def ensure(body: Ensure, request: Request, actor=Depends(scope("hf-cache:write"))):
        return response(request, ensure_item(body))

    def job_view(row):
        return {
            "id": row["id"],
            "status": row["status"],
            "progress": row["progress"],
            "phase": row["phase"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "item_id": row["item_id"],
            "error": row["error"],
            "created_at": row["created"],
            "updated_at": row["updated"],
            "requested_revision": row["requested_revision"],
            "resolved_revision": row["resolved_revision"],
        }

    def update_job(
        job_id, *, status=None, progress=None, phase=None, attempts=None, item_id=None, error=None
    ):
        fields = {"updated": time.time()}
        for name, value in (
            ("status", status),
            ("progress", progress),
            ("phase", phase),
            ("attempts", attempts),
            ("item_id", item_id),
            ("error", error),
        ):
            if value is not None:
                fields[name] = value
        with transaction(db_path) as db:
            db.execute(
                "UPDATE prefetch_jobs SET " + ",".join(f"{name}=?" for name in fields) + " WHERE id=?",
                (*fields.values(), job_id),
            )

    def run_job(job_id):
        with connect(db_path) as db:
            row = db.execute("SELECT * FROM prefetch_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            return
        body = Ensure.model_validate_json(row["payload"])
        attempt = row["attempts"]
        while attempt < row["max_attempts"]:
            attempt += 1
            update_job(job_id, status="running", attempts=attempt, progress=0, phase="queued", error="")

            def progress(value, phase):
                update_job(job_id, progress=value, phase=phase)

            try:
                result = ensure_item(body, progress, row["requested_revision"])
                update_job(
                    job_id, status="succeeded", progress=100, phase="ready", item_id=result["id"], error=""
                )
                return
            except APIError as exc:
                retryable = exc.status in (429, 502, 503)
                message = f"{exc.code}: {exc.message}"
            except Exception as exc:
                retryable = True
                message = f"{type(exc).__name__}: prefetch attempt failed"
            if retryable and attempt < row["max_attempts"]:
                update_job(job_id, status="retrying", phase="retry_wait", error=message)
                time.sleep(min(0.1 * 2 ** (attempt - 1), 1.0))
                continue
            update_job(job_id, status="failed", phase="failed", error=message)
            return

    def enqueue(body):
        requested_revision = body.revision
        resolved_revision = resolve_revision(body.repo_type, body.repo_id, requested_revision)
        if not isinstance(resolved_revision, str) or not REVISION.fullmatch(resolved_revision):
            raise APIError(502, "INVALID_HUB_REVISION", "revision resolver did not return a commit SHA")
        pinned_body = body.model_copy(update={"revision": resolved_revision})
        job_id = str(uuid4())
        now = time.time()
        with transaction(db_path) as db:
            db.execute(
                "INSERT INTO prefetch_jobs(id,payload,status,progress,phase,attempts,max_attempts,item_id,error,created,updated,requested_revision,resolved_revision) "
                "VALUES(?,?,'queued',0,'queued',0,3,NULL,'',?,?,?,?)",
                (job_id, pinned_body.model_dump_json(), now, now, requested_revision, resolved_revision),
            )
        _PREFETCH_POOL.submit(run_job, job_id)
        return job_id

    def resume_prefetch_jobs():
        with transaction(db_path) as db:
            rows = db.execute(
                "SELECT id FROM prefetch_jobs WHERE status IN ('queued','running','retrying')"
            ).fetchall()
            db.execute(
                "UPDATE dependency_candidates SET status='pending',updated=? WHERE status='accepting'",
                (time.time(),),
            )
        for row in rows:
            update_job(row["id"], status="queued", phase="queued")
            _PREFETCH_POOL.submit(run_job, row["id"])

    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        resume_prefetch_jobs()
        async with existing_lifespan(application):
            yield

    app.router.lifespan_context = lifespan

    @app.post("/api/v1/cache/prefetch", status_code=202)
    def prefetch(body: Ensure, request: Request, actor=Depends(scope("hf-cache:write"))):
        job_id = enqueue(body)
        with connect(db_path) as db:
            row = db.execute("SELECT * FROM prefetch_jobs WHERE id=?", (job_id,)).fetchone()
        return response(request, job_view(row))

    @app.get("/api/v1/cache/prefetch/jobs/{job_id}")
    def prefetch_job(job_id: str, request: Request, actor=Depends(scope("hf-cache:read"))):
        with connect(db_path) as db:
            row = db.execute("SELECT * FROM prefetch_jobs WHERE id=?", (job_id,)).fetchone()
        if not row:
            raise APIError(404, "PREFETCH_JOB_NOT_FOUND", "prefetch job not found")
        return response(request, job_view(row))

    @app.post("/api/v1/cache/prefetch/jobs/{job_id}/retry")
    def retry_prefetch(job_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        with transaction(db_path) as db:
            row = db.execute("SELECT * FROM prefetch_jobs WHERE id=?", (job_id,)).fetchone()
            if not row:
                raise APIError(404, "PREFETCH_JOB_NOT_FOUND", "prefetch job not found")
            if row["status"] != "failed":
                raise APIError(409, "PREFETCH_JOB_NOT_FAILED", "only failed prefetch jobs can be retried")
            db.execute(
                "UPDATE prefetch_jobs SET status='queued',progress=0,phase='queued',attempts=0,error='',updated=? WHERE id=?",
                (time.time(), job_id),
            )
        _PREFETCH_POOL.submit(run_job, job_id)
        return response(request, {"id": job_id, "status": "queued"})

    @app.post("/api/v1/signals/dependency")
    def dependency_signal(body: DependencySignal, request: Request, actor=Depends(scope("hf-cache:write"))):
        candidate_id = str(uuid4())
        now = time.time()
        with transaction(db_path) as db:
            db.execute(
                "INSERT INTO dependency_candidates VALUES(?,?, 'pending', ?, ?, NULL)",
                (candidate_id, body.model_dump_json(), now, now),
            )
        return response(request, {"candidate_id": candidate_id, "status": "pending_review"})

    def require_candidate_reviewer(actor):
        if actor.kind != "human" and "hf-cache:admin" not in actor.scopes:
            raise APIError(403, "HUMAN_REVIEW_REQUIRED", "candidate decisions require a human or cache admin")

    @app.get("/api/v1/prefetch/candidates")
    def candidates(request: Request, actor=Depends(scope("hf-cache:read"))):
        with connect(db_path) as db:
            rows = db.execute("SELECT * FROM dependency_candidates ORDER BY created DESC").fetchall()
        return response(
            request,
            [
                {
                    "id": row["id"],
                    "signal": json.loads(row["signal"]),
                    "status": row["status"],
                    "created_at": row["created"],
                    "job_id": row["job_id"],
                }
                for row in rows
            ],
        )

    @app.post("/api/v1/prefetch/candidates/{candidate_id}/accept")
    def accept_candidate(candidate_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        require_candidate_reviewer(actor)
        with transaction(db_path) as db:
            row = db.execute("SELECT * FROM dependency_candidates WHERE id=?", (candidate_id,)).fetchone()
            if not row:
                raise APIError(404, "CANDIDATE_NOT_FOUND", "prefetch candidate not found")
            if row["status"] != "pending":
                raise APIError(409, "CANDIDATE_ALREADY_DECIDED", "candidate is no longer pending")
            db.execute(
                "UPDATE dependency_candidates SET status='accepting',updated=? WHERE id=?",
                (time.time(), candidate_id),
            )
            signal = DependencySignal.model_validate_json(row["signal"])
        try:
            job_id = enqueue(
                Ensure(
                    repo_type=signal.repo_type,
                    repo_id=signal.repo_id,
                    revision=signal.revision,
                    priority="production" if signal.source == "deployment_manifest" else "normal",
                )
            )
        except Exception:
            with transaction(db_path) as db:
                db.execute(
                    "UPDATE dependency_candidates SET status='pending',updated=? WHERE id=? AND status='accepting'",
                    (time.time(), candidate_id),
                )
            raise
        with transaction(db_path) as db:
            db.execute(
                "UPDATE dependency_candidates SET status='accepted',job_id=?,updated=? WHERE id=? AND status='accepting'",
                (job_id, time.time(), candidate_id),
            )
        return response(request, {"candidate_id": candidate_id, "status": "accepted", "job_id": job_id})

    @app.post("/api/v1/prefetch/candidates/{candidate_id}/reject")
    def reject_candidate(candidate_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        require_candidate_reviewer(actor)
        with transaction(db_path) as db:
            row = db.execute(
                "SELECT status FROM dependency_candidates WHERE id=?", (candidate_id,)
            ).fetchone()
            if not row:
                raise APIError(404, "CANDIDATE_NOT_FOUND", "prefetch candidate not found")
            if row["status"] != "pending":
                raise APIError(409, "CANDIDATE_ALREADY_DECIDED", "candidate is no longer pending")
            db.execute(
                "UPDATE dependency_candidates SET status='rejected',updated=? WHERE id=?",
                (time.time(), candidate_id),
            )
        return response(request, {"candidate_id": candidate_id, "status": "rejected"})

    @app.get("/api/v1/cache/items")
    def items(request: Request, actor=Depends(scope("hf-cache:read"))):
        with connect(db_path) as db:
            rows = db.execute("SELECT * FROM items ORDER BY created DESC").fetchall()
            return response(request, [public_item(db, row) for row in rows])

    @app.get("/api/v1/cache/items/{item_id}")
    def item(item_id: str, request: Request, actor=Depends(scope("hf-cache:read"))):
        with transaction(db_path) as db:
            row = get_item(db, item_id)
            db.execute("UPDATE items SET accessed=? WHERE id=?", (time.time(), item_id))
            return response(request, public_item(db, row))

    @app.post("/api/v1/cache/items/{item_id}/pin")
    def pin(item_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        with transaction(db_path) as db:
            get_item(db, item_id)
            db.execute("UPDATE items SET pinned=1 WHERE id=?", (item_id,))
            return response(request, {"id": item_id, "pinned": True})

    @app.delete("/api/v1/cache/items/{item_id}/pin")
    def unpin(item_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        with transaction(db_path) as db:
            get_item(db, item_id)
            db.execute("UPDATE items SET pinned=0 WHERE id=?", (item_id,))
            return response(request, {"id": item_id, "pinned": False})

    @app.post("/api/v1/cache/items/{item_id}/evict")
    def evict(item_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        with transaction(db_path) as db:
            row = get_item(db, item_id)
            if row["pinned"]:
                raise APIError(409, "CACHE_ITEM_PROTECTED", "pinned cache items cannot be evicted")
            if db.execute("SELECT 1 FROM leases WHERE item_id=? LIMIT 1", (item_id,)).fetchone():
                raise APIError(409, "CACHE_ITEM_IN_USE", "active cache leases prevent eviction")
            item_path = Path(row["path"])
            if root not in item_path.parents or item_path.is_symlink():
                raise APIError(500, "CACHE_PATH_INVALID", "refusing to delete outside cache root")
            shutil.rmtree(item_path)
            db.execute("DELETE FROM items WHERE id=?", (item_id,))
            return response(request, {"id": item_id, "evicted": True})

    @app.post("/api/v1/cache/items/{item_id}/leases")
    def acquire_lease(item_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        with transaction(db_path) as db:
            get_item(db, item_id)
            lease_id = str(uuid4())
            db.execute("INSERT INTO leases VALUES(?,?,?,?)", (lease_id, item_id, actor.sub, time.time()))
            return response(request, {"lease_id": lease_id, "item_id": item_id})

    @app.delete("/api/v1/cache/items/{item_id}/leases/{lease_id}")
    def release_lease(item_id: str, lease_id: str, request: Request, actor=Depends(scope("hf-cache:write"))):
        with transaction(db_path) as db:
            row = db.execute(
                "SELECT owner FROM leases WHERE id=? AND item_id=?", (lease_id, item_id)
            ).fetchone()
            if not row:
                raise APIError(404, "LEASE_NOT_FOUND", "lease not found")
            if row["owner"] != actor.sub and "hf-cache:admin" not in actor.scopes:
                raise APIError(403, "LEASE_FORBIDDEN", "only lease owner or cache admin may release")
            db.execute("DELETE FROM leases WHERE id=?", (lease_id,))
            return response(request, {"lease_id": lease_id, "released": True})

    @app.get("/api/v1/cache/storage")
    def storage(request: Request, actor=Depends(scope("hf-cache:read"))):
        usage = storage_usage()
        percent = usage.pressure_percent
        level = (
            "normal"
            if percent < 80
            else "stop_prefetch"
            if percent < 85
            else "evict_low_priority"
            if percent < 90
            else "retain_protected"
        )
        with connect(db_path) as db:
            cached_bytes = db.execute("SELECT COALESCE(SUM(bytes),0) FROM items").fetchone()[0]
        return response(
            request,
            {
                "path": str(root),
                "pool": usage.pool,
                "pool_total_bytes": usage.pool_total,
                "pool_used_bytes": usage.pool_used,
                "pool_free_bytes": usage.pool_free,
                "guest_total_bytes": usage.guest_total,
                "guest_used_bytes": usage.guest_used,
                "guest_free_bytes": usage.guest_free,
                "total_bytes": usage.pool_total,
                "used_bytes": usage.pool_used,
                "free_bytes": usage.available_bytes,
                "used_percent": round(percent, 2),
                "watermark": level,
                "cached_bytes": cached_bytes,
            },
        )

    @app.post("/api/v1/cache/gc")
    def gc(body: GCRequest, request: Request, actor=Depends(scope("hf-cache:write"))):
        target_usage_percent = body.target_usage_percent
        removed, usage = collect(target_usage_percent)
        return response(
            request,
            {
                "evicted": removed,
                "remaining_usage_percent": round(usage.pressure_percent, 2),
                "target_usage_percent": target_usage_percent,
                "target_reached": usage.pressure_percent <= target_usage_percent,
            },
        )

    return app
