"""Persistent, bounded multipart upload sessions for XeLibrary attachments."""

import fcntl
import hashlib
import math
import os
import re
import shutil
from contextlib import contextmanager
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, connect, scope, transaction

PART_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_ACTIVE_PER_ACTOR = 3
MAX_ACTIVE_GLOBAL = 1000
MAX_RESERVED_BYTES_PER_ACTOR = 4 * 1024 * 1024 * 1024
MAX_RESERVED_BYTES_GLOBAL = 20 * 1024 * 1024 * 1024
SESSION_TTL_SECONDS = 24 * 60 * 60


class UploadStart(BaseModel):
    table_id: str = Field(min_length=1, max_length=64)
    row_id: str = Field(min_length=1, max_length=64)
    filename: str = Field(min_length=1, max_length=1024)
    size: int = Field(ge=1, le=MAX_UPLOAD_BYTES)


def register_upload_routes(
    app, *, root, authorize, file, load_row, audit, attachment_metadata, response, now
):
    state_db = root / "uploads.sqlite"
    part_root = root / "objects" / ".uploads"
    part_root.mkdir(parents=True, exist_ok=True)
    lock_root = part_root / ".locks"
    lock_root.mkdir(mode=0o700, exist_ok=True)
    with transaction(state_db) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS uploads(
          upload_id TEXT PRIMARY KEY, attachment_id TEXT NOT NULL UNIQUE, actor TEXT NOT NULL,
          table_id TEXT NOT NULL, row_id TEXT NOT NULL, filename TEXT NOT NULL, size INTEGER NOT NULL,
          part_size INTEGER NOT NULL, part_count INTEGER NOT NULL, created_at TEXT NOT NULL,
          completed INTEGER NOT NULL DEFAULT 0, cancelled INTEGER NOT NULL DEFAULT 0,
          completing INTEGER NOT NULL DEFAULT 0,
          object_hash TEXT, media_type TEXT);
        CREATE TABLE IF NOT EXISTS upload_parts(
          upload_id TEXT NOT NULL, part INTEGER NOT NULL, size INTEGER NOT NULL, sha256 TEXT NOT NULL,
          PRIMARY KEY(upload_id,part), FOREIGN KEY(upload_id) REFERENCES uploads(upload_id) ON DELETE CASCADE);
        """)
        existing_columns = {row[1] for row in db.execute("PRAGMA table_info(uploads)")}
        if "object_hash" not in existing_columns:
            db.execute("ALTER TABLE uploads ADD COLUMN object_hash TEXT")
        if "media_type" not in existing_columns:
            db.execute("ALTER TABLE uploads ADD COLUMN media_type TEXT")
        if "completing" not in existing_columns:
            db.execute("ALTER TABLE uploads ADD COLUMN completing INTEGER NOT NULL DEFAULT 0")

    def session(upload_id, actor):
        try:
            # Reject non-UUID identifiers before using them to construct any path.
            normalized = str(UUID(upload_id))
        except (ValueError, AttributeError):
            raise APIError(404, "UPLOAD_NOT_FOUND", "upload session not found") from None
        with connect(state_db) as db:
            row = db.execute("SELECT * FROM uploads WHERE upload_id=?", (normalized,)).fetchone()
        if row is None or row["actor"] != actor.sub:
            raise APIError(404, "UPLOAD_NOT_FOUND", "upload session not found")
        if row["cancelled"]:
            raise APIError(410, "UPLOAD_CANCELLED", "upload session was cancelled")
        authorize(actor, row["table_id"], "attach", row["row_id"])
        with connect(file(row["table_id"])) as db:
            load_row(db, row["row_id"])
        return row

    def metadata(db, attachment_id):
        row = db.execute("SELECT * FROM __xl_attachments WHERE attachment_id=?", (attachment_id,)).fetchone()
        return attachment_metadata(row) if row else None

    def file_hash(path):
        digest = hashlib.sha256()
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def sync_directory(path):
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @contextmanager
    def upload_lock(upload_id):
        lock_path = lock_root / f"{upload_id}.lock"
        with lock_path.open("a+b") as lock_file:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise APIError(409, "UPLOAD_COMPLETING", "upload completion is already in progress") from None
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @app.post("/api/v1/uploads", status_code=201)
    def start_upload(body: UploadStart, request: Request, actor=Depends(scope("xelibrary:attach"))):
        authorize(actor, body.table_id, "attach", body.row_id)
        with connect(file(body.table_id)) as db:
            load_row(db, body.row_id)
        safe_filename = re.sub(r"[^\w. -]", "_", PurePosixPath(body.filename).name)[:128] or "attachment"
        count = math.ceil(body.size / PART_BYTES)
        upload_id, attachment_id = str(uuid4()), str(uuid4())
        stamp = now()
        with transaction(state_db) as db:
            # Bound both abandoned sessions and per-caller resource consumption.
            from datetime import datetime, timedelta, timezone

            cutoff = (
                (datetime.now(timezone.utc) - timedelta(seconds=SESSION_TTL_SECONDS))
                .isoformat()
                .replace("+00:00", "Z")
            )
            expired = db.execute("SELECT upload_id FROM uploads WHERE created_at<?", (cutoff,)).fetchall()
            for stale in expired:
                shutil.rmtree(part_root / stale["upload_id"], ignore_errors=True)
                (lock_root / f"{stale['upload_id']}.lock").unlink(missing_ok=True)
                db.execute("DELETE FROM uploads WHERE upload_id=?", (stale["upload_id"],))
            actor_count = db.execute(
                "SELECT count(*) FROM uploads WHERE actor=? AND completed=0 AND cancelled=0", (actor.sub,)
            ).fetchone()[0]
            total_count = db.execute(
                "SELECT count(*) FROM uploads WHERE completed=0 AND cancelled=0"
            ).fetchone()[0]
            actor_bytes = db.execute(
                "SELECT coalesce(sum(size),0) FROM uploads WHERE actor=? AND completed=0 AND cancelled=0",
                (actor.sub,),
            ).fetchone()[0]
            total_bytes = db.execute(
                "SELECT coalesce(sum(size),0) FROM uploads WHERE completed=0 AND cancelled=0"
            ).fetchone()[0]
            if (
                actor_count >= MAX_ACTIVE_PER_ACTOR
                or total_count >= MAX_ACTIVE_GLOBAL
                or actor_bytes + body.size > MAX_RESERVED_BYTES_PER_ACTOR
                or total_bytes + body.size > MAX_RESERVED_BYTES_GLOBAL
            ):
                raise APIError(429, "UPLOAD_LIMIT", "too many active upload sessions")
            db.execute(
                "INSERT INTO uploads(upload_id,attachment_id,actor,table_id,row_id,filename,size,part_size,part_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    upload_id,
                    attachment_id,
                    actor.sub,
                    body.table_id,
                    body.row_id,
                    safe_filename,
                    body.size,
                    PART_BYTES,
                    count,
                    stamp,
                ),
            )
        (part_root / upload_id).mkdir(mode=0o700)
        audit(actor, "attachment.upload.started", f"{body.table_id}:{body.row_id}:{upload_id}")
        return response(
            request, {"upload_id": upload_id, "part_size": PART_BYTES, "part_count": count, "size": body.size}
        )

    @app.put(
        "/api/v1/uploads/{upload_id}/parts/{part}",
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}},
            }
        },
    )
    async def put_part(upload_id: str, part: int, request: Request, actor=Depends(scope("xelibrary:attach"))):
        current = session(upload_id, actor)
        if current["completed"]:
            raise APIError(409, "UPLOAD_COMPLETED", "upload is already complete")
        if part < 1 or part > current["part_count"]:
            raise APIError(422, "INVALID_PART", "part number is outside the upload")
        expected_size = min(current["part_size"], current["size"] - (part - 1) * current["part_size"])
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) != expected_size):
            raise APIError(422, "PART_SIZE_MISMATCH", "part size does not match the upload")
        chunks, size, digest = [], 0, hashlib.sha256()
        async for chunk in request.stream():
            size += len(chunk)
            if size > expected_size:
                raise APIError(413, "PART_TOO_LARGE", "part exceeds its declared size")
            digest.update(chunk)
            chunks.append(chunk)
        if size != expected_size:
            raise APIError(422, "PART_SIZE_MISMATCH", "part size does not match the upload")
        checksum = digest.hexdigest()
        with transaction(state_db) as db:
            latest = db.execute(
                "SELECT completed,cancelled,completing FROM uploads WHERE upload_id=?",
                (current["upload_id"],),
            ).fetchone()
            if latest is None or latest["cancelled"]:
                raise APIError(410, "UPLOAD_CANCELLED", "upload session was cancelled")
            if latest["completed"] or latest["completing"]:
                raise APIError(409, "UPLOAD_COMPLETING", "upload completion is in progress")
            old = db.execute(
                "SELECT * FROM upload_parts WHERE upload_id=? AND part=?", (current["upload_id"], part)
            ).fetchone()
            if old:
                if old["sha256"] != checksum or old["size"] != size:
                    raise APIError(409, "PART_CONFLICT", "part already exists with different content")
                return response(
                    request,
                    {"upload_id": current["upload_id"], "part": part, "sha256": checksum, "reused": True},
                )
            directory = part_root / current["upload_id"]
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            target = directory / f"{part:08d}.part"
            temporary = directory / f".{part:08d}.{uuid4()}.tmp"
            with temporary.open("xb") as output:
                for chunk in chunks:
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
            sync_directory(directory)
            db.execute(
                "INSERT INTO upload_parts VALUES(?,?,?,?)", (current["upload_id"], part, size, checksum)
            )
        return response(
            request, {"upload_id": current["upload_id"], "part": part, "sha256": checksum, "reused": False}
        )

    def _complete_upload_locked(upload_id: str, request: Request, actor):
        current = session(upload_id, actor)
        directory = part_root / current["upload_id"]
        if current["completed"]:
            with connect(file(current["table_id"])) as db:
                attached = metadata(db, current["attachment_id"])
            if attached:
                shutil.rmtree(directory, ignore_errors=True)
                with transaction(state_db) as db:
                    db.execute("DELETE FROM upload_parts WHERE upload_id=?", (current["upload_id"],))
                return response(request, attached, idempotent=True)
            # A backup can straddle SQLite files. The completed marker and durable object
            # digest let a restored session repair its missing .xl reference without parts.
            if current["object_hash"] and current["media_type"]:
                object_path = (
                    root
                    / "objects"
                    / "sha256"
                    / current["object_hash"][:2]
                    / current["object_hash"][2:4]
                    / current["object_hash"]
                )
                if not object_path.is_file():
                    raise APIError(500, "OBJECT_STORE_CORRUPT", "completed upload object is missing")
                with transaction(file(current["table_id"])) as db:
                    authorize(actor, current["table_id"], "attach", current["row_id"])
                    load_row(db, current["row_id"])
                    attached_row = db.execute(
                        "SELECT * FROM __xl_attachments WHERE attachment_id=?", (current["attachment_id"],)
                    ).fetchone()
                    if attached_row is None:
                        db.execute(
                            "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
                            (
                                current["attachment_id"],
                                current["row_id"],
                                current["object_hash"],
                                current["filename"],
                                current["media_type"],
                                object_path.stat().st_size,
                                now(),
                                "{}",
                            ),
                        )
                        db.execute(
                            "UPDATE __xl_meta SET table_revision=table_revision+1,updated_at=?", (now(),)
                        )
                        attached_row = db.execute(
                            "SELECT * FROM __xl_attachments WHERE attachment_id=?",
                            (current["attachment_id"],),
                        ).fetchone()
                shutil.rmtree(directory, ignore_errors=True)
                with transaction(state_db) as db:
                    db.execute("DELETE FROM upload_parts WHERE upload_id=?", (current["upload_id"],))
                return response(request, attachment_metadata(attached_row), idempotent=True, repaired=True)
        with connect(state_db) as db:
            parts = db.execute(
                "SELECT part,size,sha256 FROM upload_parts WHERE upload_id=? ORDER BY part",
                (current["upload_id"],),
            ).fetchall()
        expected = [
            (n, min(current["part_size"], current["size"] - (n - 1) * current["part_size"]))
            for n in range(1, current["part_count"] + 1)
        ]
        if [(item["part"], item["size"]) for item in parts] != expected:
            raise APIError(409, "UPLOAD_INCOMPLETE", "all parts must be uploaded before completion")
        with transaction(state_db) as db:
            latest = db.execute(
                "SELECT cancelled,completed FROM uploads WHERE upload_id=?", (current["upload_id"],)
            ).fetchone()
            if latest is None or latest["cancelled"]:
                raise APIError(410, "UPLOAD_CANCELLED", "upload session was cancelled")
            if not latest["completed"]:
                db.execute("UPDATE uploads SET completing=1 WHERE upload_id=?", (current["upload_id"],))
        assembled = part_root / f".{current['upload_id']}.{uuid4()}.tmp"
        digest, size = hashlib.sha256(), 0
        try:
            with assembled.open("xb") as output:
                for number, expected_size in expected:
                    path = directory / f"{number:08d}.part"
                    part_record = parts[number - 1]
                    part_digest, part_size = hashlib.sha256(), 0
                    with path.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            size += len(chunk)
                            part_size += len(chunk)
                            part_digest.update(chunk)
                            digest.update(chunk)
                            output.write(chunk)
                    if part_size != expected_size or part_digest.hexdigest() != part_record["sha256"]:
                        raise APIError(
                            409, "UPLOAD_PART_CORRUPT", f"part {number} failed integrity validation"
                        )
                output.flush()
                os.fsync(output.fileno())
            if size != current["size"]:
                raise APIError(409, "UPLOAD_INCOMPLETE", "uploaded bytes do not match declared size")
            hexdigest = digest.hexdigest()
            target = root / "objects" / "sha256" / hexdigest[:2] / hexdigest[2:4] / hexdigest
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                if target.stat().st_size != size or file_hash(target) != hexdigest:
                    raise APIError(
                        500, "OBJECT_STORE_CORRUPT", "existing content-addressed object is invalid"
                    )
                assembled.unlink()
            else:
                os.replace(assembled, target)
                sync_directory(target.parent)
            with target.open("rb") as source:
                prefix = source.read(8)
            media = (
                "application/pdf"
                if prefix.startswith(b"%PDF-")
                else ("image/png" if prefix == b"\x89PNG\r\n\x1a\n" else "application/octet-stream")
            )
            with transaction(state_db) as db:
                db.execute(
                    "UPDATE uploads SET object_hash=?,media_type=? WHERE upload_id=?",
                    (hexdigest, media, current["upload_id"]),
                )
            # A stable, preallocated attachment ID makes retry safe if the process dies
            # after committing the .xl fact but before marking this session complete.
            with transaction(file(current["table_id"])) as db:
                authorize(actor, current["table_id"], "attach", current["row_id"])
                load_row(db, current["row_id"])
                existing = db.execute(
                    "SELECT * FROM __xl_attachments WHERE attachment_id=?", (current["attachment_id"],)
                ).fetchone()
                created = existing is None
                if existing is None:
                    db.execute(
                        "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
                        (
                            current["attachment_id"],
                            current["row_id"],
                            hexdigest,
                            current["filename"],
                            media,
                            size,
                            now(),
                            "{}",
                        ),
                    )
                    db.execute("UPDATE __xl_meta SET table_revision=table_revision+1,updated_at=?", (now(),))
                    existing = db.execute(
                        "SELECT * FROM __xl_attachments WHERE attachment_id=?", (current["attachment_id"],)
                    ).fetchone()
            with transaction(state_db) as db:
                db.execute(
                    "UPDATE uploads SET completed=1,completing=0 WHERE upload_id=?", (current["upload_id"],)
                )
                db.execute("DELETE FROM upload_parts WHERE upload_id=?", (current["upload_id"],))
            shutil.rmtree(directory, ignore_errors=True)
            audit(
                actor,
                "attachment.created",
                f"{current['table_id']}:{current['row_id']}:{current['attachment_id']}",
            )
            return response(request, attachment_metadata(existing), idempotent=not created)
        except BaseException:
            with transaction(state_db) as db:
                db.execute(
                    "UPDATE uploads SET completing=0 WHERE upload_id=? AND completed=0",
                    (current["upload_id"],),
                )
            raise
        finally:
            assembled.unlink(missing_ok=True)

    @app.post("/api/v1/uploads/{upload_id}/complete")
    def complete_upload(upload_id: str, request: Request, actor=Depends(scope("xelibrary:attach"))):
        current = session(upload_id, actor)
        with upload_lock(current["upload_id"]):
            return _complete_upload_locked(upload_id, request, actor)

    @app.delete("/api/v1/uploads/{upload_id}")
    def cancel_upload(upload_id: str, request: Request, actor=Depends(scope("xelibrary:attach"))):
        try:
            from uuid import UUID

            upload_id = str(UUID(upload_id))
        except ValueError:
            raise APIError(404, "UPLOAD_NOT_FOUND", "upload session not found") from None
        with connect(state_db) as db:
            current = db.execute("SELECT * FROM uploads WHERE upload_id=?", (upload_id,)).fetchone()
        if current is None or current["actor"] != actor.sub:
            raise APIError(404, "UPLOAD_NOT_FOUND", "upload session not found")
        authorize(actor, current["table_id"], "attach", current["row_id"])
        if current["completed"]:
            raise APIError(409, "UPLOAD_COMPLETED", "completed upload cannot be cancelled")
        with transaction(state_db) as db:
            latest = db.execute(
                "SELECT completing,completed,cancelled FROM uploads WHERE upload_id=?", (upload_id,)
            ).fetchone()
            if latest["completed"]:
                raise APIError(409, "UPLOAD_COMPLETED", "completed upload cannot be cancelled")
            if latest["completing"]:
                raise APIError(409, "UPLOAD_COMPLETING", "upload completion is in progress")
            db.execute("UPDATE uploads SET cancelled=1 WHERE upload_id=?", (upload_id,))
            db.execute("DELETE FROM upload_parts WHERE upload_id=?", (upload_id,))
        shutil.rmtree(part_root / upload_id, ignore_errors=True)
        audit(actor, "attachment.upload.cancelled", f"{current['table_id']}:{current['row_id']}:{upload_id}")
        return response(request, {"upload_id": upload_id, "cancelled": True})
