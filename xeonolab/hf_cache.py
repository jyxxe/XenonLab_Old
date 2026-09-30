"""Revision-pinned Hugging Face snapshot cache with durable manifests and leases."""

import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, response, scope, transaction

REVISION = re.compile(r"^[0-9a-f]{40}$")
_ENSURE_LOCK = threading.Lock()


class Ensure(BaseModel):
    repo_type: str = Field(default="model", pattern="^(model|dataset|space)$")
    repo_id: str = Field(min_length=3, max_length=256, pattern=r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
    revision: str = Field(min_length=40, max_length=40, pattern=r"^[0-9a-f]{40}$")
    allow_patterns: list[str] = Field(default_factory=list, max_length=128)
    ignore_patterns: list[str] = Field(default_factory=list, max_length=128)
    priority: str = Field(default="normal", pattern="^(low|normal|production)$")
    pin: bool = False


class GCRequest(BaseModel):
    target_usage_percent: float = Field(default=75, gt=0, le=100)


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


def create_app(settings=None, *, download_backend=None):
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

    backend = download_backend or _hub_download
    try:
        cache_quota = int(os.getenv("HF_CACHE_MAX_BYTES", str(2 * 1024**4)))
    except ValueError:
        raise RuntimeError("HF_CACHE_MAX_BYTES must be a positive integer") from None
    if cache_quota <= 0:
        raise RuntimeError("HF_CACHE_MAX_BYTES must be a positive integer")
    app.state.ready_check = lambda: True

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
        usage = shutil.disk_usage(root)
        removed = []
        priority_filter = "AND i.priority='low'" if low_only else ""
        with transaction(db_path) as db:
            rows = db.execute(
                f"""SELECT i.* FROM items i WHERE i.pinned=0 {priority_filter}
                AND NOT EXISTS (SELECT 1 FROM leases l WHERE l.item_id=i.id)
                ORDER BY CASE i.priority WHEN 'low' THEN 0 WHEN 'normal' THEN 1 ELSE 2 END, i.accessed ASC"""
            ).fetchall()
            for row in rows:
                if usage.used * 100 / usage.total <= target_percent:
                    break
                item_path = Path(row["path"])
                if root not in item_path.parents or item_path.is_symlink():
                    raise APIError(500, "CACHE_PATH_INVALID", "refusing to delete outside cache root")
                shutil.rmtree(item_path)
                db.execute("DELETE FROM items WHERE id=?", (row["id"],))
                removed.append(row["id"])
                usage = shutil.disk_usage(root)
        return removed, usage

    @app.post("/api/v1/cache/ensure")
    def ensure(body: Ensure, request: Request, actor=Depends(scope("hf-cache:write"))):
        key = _canonical(body)
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
                    return response(request, {"status": "cached", **public_item(db, row)})
            if old:
                raise APIError(500, "CACHE_INTEGRITY_FAILURE", "cached snapshot failed manifest verification")
            item_id = str(uuid4())
            final = root / item_id
            staging = root / ("." + item_id + ".partial")
            try:
                usage = shutil.disk_usage(root)
                used_percent = usage.used * 100 / usage.total if usage.total else 100
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
                    usage = shutil.disk_usage(root)
                    used_percent = usage.used * 100 / usage.total if usage.total else 100
                    if used_percent >= 90:
                        raise APIError(
                            507, "DISK_WATERMARK", "cache downloads disabled above the 90% disk watermark"
                        )
                with connect(db_path) as db:
                    current_bytes = db.execute("SELECT COALESCE(SUM(bytes),0) FROM items").fetchone()[0]
                remaining = cache_quota - current_bytes
                if remaining <= 0:
                    raise APIError(507, "CACHE_QUOTA_EXCEEDED", "HF-cache byte quota is full")
                backend(
                    body.repo_type,
                    body.repo_id,
                    body.revision,
                    staging,
                    body.allow_patterns,
                    body.ignore_patterns,
                    remaining,
                )
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
                        "INSERT INTO items VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            item_id,
                            key,
                            body.repo_type,
                            body.repo_id,
                            body.revision,
                            json.dumps(body.allow_patterns),
                            json.dumps(body.ignore_patterns),
                            body.priority,
                            str(final),
                            json.dumps(manifest, sort_keys=True),
                            total,
                            now,
                            now,
                            int(body.pin),
                        ),
                    )
                    row = get_item(db, item_id)
                    return response(request, {"status": "ready", **public_item(db, row)})
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                if final.exists():
                    shutil.rmtree(final, ignore_errors=True)
                raise

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
        usage = shutil.disk_usage(root)
        percent = usage.used * 100 / usage.total if usage.total else 100
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
                "total_bytes": usage.total,
                "used_bytes": usage.used,
                "free_bytes": usage.free,
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
                "remaining_usage_percent": round(usage.used * 100 / usage.total, 2),
                "target_usage_percent": target_usage_percent,
                "target_reached": usage.used * 100 / usage.total <= target_usage_percent,
            },
        )

    return app
