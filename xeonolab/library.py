import base64
import binascii
import csv
import fcntl
import hashlib
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import stat
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID, uuid4

from fastapi import BackgroundTasks, Depends, Header, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from .bulk_jobs import BulkJobStore, payload_digest
from .common import APIError, base_app, connect, etag, precondition, response, scope, transaction
from .library_doctor import scan_library
from .quality_pipeline import ranked_fusion
from .query import evaluate, json_ast, parse, path_matches, sort_key
from .search_models import SEARCH_ERRORS, SearchResponse
from .uploads import register_upload_routes

TYPES = {
    "string",
    "text",
    "int64",
    "float64",
    "boolean",
    "datetime",
    "date",
    "enum",
    "json",
    "reference",
    "attachment_list",
    "theorem_ref",
}

# FTS5's unicode61 tokenizer treats an unspaced CJK sentence as one token. Keep
# the original index untouched for rollback compatibility and maintain a second,
# additive index whose CJK characters are separated into searchable tokens.
CJK_RANGES = (
    (0x3040, 0x30FF),  # Hiragana / Katakana
    (0x3400, 0x4DBF),  # CJK extension A
    (0x4E00, 0x9FFF),  # CJK unified ideographs
    (0xF900, 0xFAFF),  # CJK compatibility ideographs
    (0xAC00, 0xD7AF),  # Hangul syllables
)


def contains_cjk(text):
    return any(any(start <= ord(char) <= end for start, end in CJK_RANGES) for char in text)


def cjk_fts_body(values):
    if isinstance(values, str):
        values = [values]
    text = " ".join(str(value) for value in values)
    segmented = []
    for char in text:
        if contains_cjk(char):
            if segmented and not segmented[-1].isspace():
                segmented.append(" ")
            segmented.extend((char, " "))
        else:
            segmented.append(char)
    return " ".join("".join(segmented).split())


def cjk_fts_query(query):
    clauses = []
    for term in query.split():
        # The serialized body is already tokenized: CJK characters are
        # separated, while contiguous Latin text remains one unicode61 token.
        tokens = cjk_fts_body(term).split()
        if tokens:
            clauses.append('"' + " ".join(token.replace('"', '""') for token in tokens) + '"')
    return " AND ".join(clauses)


def ensure_cjk_fts(db):
    db.execute(
        "CREATE VIRTUAL TABLE IF NOT EXISTS __xl_fts_cjk "
        "USING fts5(row_id UNINDEXED,body, tokenize='unicode61')"
    )


def cjk_fts_exists(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='__xl_fts_cjk'").fetchone() is not None


def index_cjk_row(db, row_id, values):
    ensure_cjk_fts(db)
    db.execute("DELETE FROM __xl_fts_cjk WHERE row_id=?", (row_id,))
    db.execute("INSERT INTO __xl_fts_cjk VALUES(?,?)", (row_id, cjk_fts_body(values)))


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


SORT_CURSOR_PREFIX = "sort1."


def encode_rows_sort_cursor(payload):
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return SORT_CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_rows_sort_cursor(cursor):
    if not cursor.startswith(SORT_CURSOR_PREFIX):
        raise APIError(422, "INVALID_CURSOR", "cursor does not match the requested sort")
    encoded = cursor[len(SORT_CURSOR_PREFIX) :]
    try:
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        payload = json.loads(raw)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise APIError(422, "INVALID_CURSOR", "cursor is malformed") from None
    if not isinstance(payload, dict):
        raise APIError(422, "INVALID_CURSOR", "cursor is malformed")
    return payload


class Column(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z\u0080-\uffff][A-Za-z0-9_\u0080-\uffff]{0,63}$")
    type: str
    nullable: bool = True
    default_value: object = None


class TableCreate(BaseModel):
    path: str
    name: str = Field(min_length=1, max_length=128)
    columns: list[Column] = Field(min_length=1, max_length=128)


class RowWrite(BaseModel):
    values: dict


class BulkInsert(BaseModel):
    rows: list[RowWrite] = Field(min_length=1)
    batch_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class ImportBatchItem(BaseModel):
    key: str = Field(min_length=1, max_length=256)
    values: dict


class ImportBatch(BaseModel):
    rows: list[ImportBatchItem] = Field(min_length=1, max_length=1000)


class BulkUpdateItem(BaseModel):
    row_id: str
    revision: int = Field(ge=1)
    values: dict


class BulkUpdate(BaseModel):
    rows: list[BulkUpdateItem] = Field(min_length=1)
    batch_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class BulkDeleteItem(BaseModel):
    row_id: str
    revision: int = Field(ge=1)


class BulkDelete(BaseModel):
    rows: list[BulkDeleteItem] = Field(min_length=1)
    batch_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class AttachmentGcRequest(BaseModel):
    dry_run: bool = True
    grace_hours: int = Field(default=24, ge=1, le=720)
    limit: int = Field(default=1000, ge=1, le=100000)
    confirm: str | None = Field(default=None, min_length=16, max_length=128)


class PruneChangesRequest(BaseModel):
    dry_run: bool = True
    older_than_days: int = Field(default=30, ge=1, le=3650)
    limit: int = Field(default=5000, ge=1, le=100000)
    keep_recent: int = Field(default=50000, ge=1, le=1000000)


class RepairRequest(BaseModel):
    dry_run: bool = True
    plan_id: str | None = Field(default=None, min_length=1, max_length=64)


class ExportRequest(BaseModel):
    table_id: str
    format: Literal["jsonl", "csv"] = "jsonl"
    include_deleted: bool = False
    batch_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class ImportRequest(BaseModel):
    table_id: str
    content: str = Field(min_length=1)
    format: Literal["jsonl", "csv"] = "jsonl"
    batch_id: str | None = Field(default=None, min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


class SchemaWrite(BaseModel):
    columns: list[Column] = Field(min_length=1, max_length=128)


class QueryBody(BaseModel):
    expression: str


class Move(BaseModel):
    path: str


class Restore(BaseModel):
    revision: int = Field(ge=1)


class AttachmentBind(BaseModel):
    attachment_id: str = Field(min_length=1, max_length=64)


class AttachmentCandidate(BaseModel):
    attachment_id: str
    row_id: str
    filename: str
    media_type: str
    size: int
    created_at: str
    metadata: dict
    source_label: str


class AttachmentCandidatePage(BaseModel):
    data: list[AttachmentCandidate]
    meta: dict


class ACLWrite(BaseModel):
    grants: dict[str, list[str]]


def csv_value_coercion(column, raw):
    """Convert a CSV cell to the schema-typed value for import (issue #84)."""
    kind = column["type"]
    if raw is None or raw == "":
        return None
    if kind in ("string", "text", "date", "datetime", "enum", "reference", "theorem_ref"):
        return raw
    if kind == "int64":
        return int(raw)
    if kind == "float64":
        return float(raw)
    if kind == "boolean":
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        raise ValueError(f"invalid boolean {raw!r}")
    if kind in ("json", "attachment_list"):
        return json.loads(raw)
    return raw


def clean_path(path):
    if not path.startswith("/") or ".." in path.split("/") or len(path) > 1024:
        raise APIError(422, "INVALID_PATH", "absolute logical path required")
    return "/" + "/".join(part for part in path.split("/") if part and part != ".")


def create_app(settings=None):
    app = base_app("xelibrary", settings)
    metrics = app.state.metrics
    root = app.state.settings.data_dir
    (root / "tables").mkdir(exist_ok=True)
    (root / "objects" / "sha256").mkdir(parents=True, exist_ok=True)
    catalogue = root / "catalogue.sqlite"
    bulk_jobs = BulkJobStore(catalogue)
    with transaction(catalogue) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS tables(id TEXT PRIMARY KEY, path TEXT, name TEXT,
          owner TEXT, UNIQUE(path,name));
        CREATE TABLE IF NOT EXISTS acl(resource_type TEXT, resource_id TEXT, grants TEXT,
          PRIMARY KEY(resource_type,resource_id));
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, actor TEXT, event TEXT, resource TEXT, at TEXT);
        CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY, kind TEXT NOT NULL, table_id TEXT NOT NULL,
          state TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          error TEXT);
        CREATE TABLE IF NOT EXISTS repair_plans(id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
          plan TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0);
        """)
        acl_columns = {row[1] for row in db.execute("PRAGMA table_info(acl)")}
        if "revision" not in acl_columns:
            db.execute("ALTER TABLE acl ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "explicit" not in acl_columns:
            db.execute("ALTER TABLE acl ADD COLUMN explicit INTEGER NOT NULL DEFAULT 1")

    table_directory = root / "tables"

    def cleanup_table_uploads(tid):
        upload_database = root / "uploads.sqlite"
        if not upload_database.exists():
            return
        with connect(upload_database) as db:
            upload_ids = [row[0] for row in db.execute("SELECT upload_id FROM uploads WHERE table_id=?", (tid,))]
        with transaction(upload_database) as db:
            db.execute("DELETE FROM uploads WHERE table_id=?", (tid,))
        upload_root = root / "objects" / ".uploads"
        for upload_id in upload_ids:
            try:
                normalized_upload_id = str(UUID(upload_id))
            except ValueError:
                continue
            shutil.rmtree(upload_root / normalized_upload_id, ignore_errors=True)
            (upload_root / ".locks" / f"{normalized_upload_id}.lock").unlink(missing_ok=True)

    def recover_table_deletions():
        """Finish or undo an interrupted table deletion before serving requests."""
        with connect(catalogue) as db:
            table_ids = {row[0] for row in db.execute("SELECT id FROM tables")}
        for tombstone in table_directory.glob(".*.xl.deleting"):
            table_id = tombstone.name[1 : -len(".xl.deleting")]
            try:
                if str(UUID(table_id)) != table_id:
                    continue
            except ValueError:
                continue
            with table_lock(table_id):
                target = table_directory / f"{table_id}.xl"
                if table_id in table_ids:
                    if not target.exists():
                        os.replace(tombstone, target)
                    else:
                        tombstone.unlink(missing_ok=True)
                else:
                    cleanup_table_uploads(table_id)
                    tombstone.unlink(missing_ok=True)
        # A crash after setting the durable marker but before renaming left the
        # canonical table in place. The catalogue decides that this deletion rolls back.
        for table_id in table_ids:
            with table_lock(table_id):
                target = table_directory / f"{table_id}.xl"
                if target.is_file():
                    with transaction(target) as db:
                        columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
                        if "deleting" in columns:
                            db.execute("UPDATE __xl_meta SET deleting=0")

    def audit(actor, event, resource):
        with transaction(catalogue) as db:
            db.execute(
                "INSERT INTO audit VALUES(?,?,?,?,?)", (str(uuid4()), actor.sub, event, resource, now())
            )

    # Durable multi-resource intent journal (Issue #81). The intent is committed
    # before the filesystem/catalogue work so a crash leaves a reproducible
    # operation record instead of a silent half-finished table lifecycle.
    def begin_operation(kind, table_id, payload):
        operation_id, stamp = str(uuid4()), now()
        with transaction(catalogue) as db:
            db.execute(
                "INSERT INTO operations VALUES(?,?,?,?,?,?,?,NULL)",
                (operation_id, kind, table_id, "pending", json.dumps(payload, ensure_ascii=False), stamp, stamp),
            )
        return operation_id

    def finish_operation(operation_id):
        with transaction(catalogue) as db:
            db.execute(
                "UPDATE operations SET state='completed',updated_at=? WHERE id=? AND state='pending'",
                (now(), operation_id),
            )

    def abort_operation(operation_id, error):
        with transaction(catalogue) as db:
            db.execute(
                "UPDATE operations SET state='aborted',error=?,updated_at=? WHERE id=? AND state='pending'",
                (str(error)[:500], now(), operation_id),
            )

    def recover_lifecycle_operations():
        """Finish or roll back interrupted table lifecycle intents before serving requests.

        Existing file-level recovery (``recover_table_deletions``) runs first, so this
        pass reconciles only the durable journal and removes files proven unpublished
        by an interrupted ``table.create`` intent.
        """
        with connect(catalogue) as db:
            pending = [dict(row) for row in db.execute(
                "SELECT * FROM operations WHERE state='pending' ORDER BY created_at"
            )]
            catalogue_paths = {
                row["id"]: row["path"] for row in db.execute("SELECT id,path FROM tables")
            }
        table_ids = set(catalogue_paths)
        for operation in pending:
            operation_id, kind, table_id = operation["id"], operation["kind"], operation["table_id"]
            state, note = "completed", None
            if kind == "table.create":
                target = table_directory / f"{table_id}.xl"
                if table_id not in table_ids and target.is_file():
                    # The intent proves this file was never published to the catalogue.
                    try:
                        with connect(target) as db:
                            meta = db.execute("SELECT table_id FROM __xl_meta").fetchone()
                        if meta is None or meta[0] == table_id:
                            target.unlink()
                            note = "removed unpublished table file"
                    except sqlite3.DatabaseError:
                        target.unlink(missing_ok=True)
                        note = "removed unpublished, unreadable table file"
            elif kind == "table.delete":
                if table_id in table_ids:
                    state = "aborted"
                    note = "table still in catalogue; deletion rolled back"
            elif kind == "table.move":
                if table_id not in catalogue_paths:
                    state, note = "aborted", "table is no longer in the catalogue"
                else:
                    try:
                        payload = json.loads(operation["payload"])
                    except (TypeError, ValueError):
                        payload = {}
                    target_path = catalogue_paths[table_id]
                    source_path = payload.get("from")
                    table_file = table_directory / f"{table_id}.xl"
                    if not table_file.is_file():
                        state, note = "aborted", "table file is missing; consistency scan reports the gap"
                    elif target_path == source_path:
                        # The catalogue commit never happened, so the move rolled back.
                        state, note = "aborted", "move did not commit; catalogue path is unchanged"
                    else:
                        with table_lock(table_id), transaction(table_file) as db:
                            meta = dict(db.execute("SELECT * FROM __xl_meta").fetchone() or {})
                            if meta.get("deleting"):
                                state, note = "aborted", "table is being deleted"
                            elif meta.get("logical_path") != target_path:
                                db.execute(
                                    "UPDATE __xl_meta SET logical_path=?,table_revision=table_revision+1,"
                                    "updated_at=?",
                                    (target_path, now()),
                                )
                                note = f"reconciled table metadata path to catalogue path {target_path!r}"
            else:
                state = "aborted"
                note = f"unsupported operation kind {kind!r}"
            with transaction(catalogue) as db:
                db.execute(
                    "UPDATE operations SET state=?,error=COALESCE(?,error),updated_at=? WHERE id=?",
                    (state, note, now(), operation_id),
                )
                db.execute(
                    "INSERT INTO audit VALUES(?,?,?,?,?)",
                    (str(uuid4()), "system:recovery", "operation.recovered", operation_id, now()),
                )
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat().replace("+00:00", "Z")
        with transaction(catalogue) as db:
            db.execute("DELETE FROM operations WHERE state!='pending' AND created_at<?", (cutoff,))

    # Explicit, dry-run-first repair plans for the safe subset of consistency findings
    # (Issue #81). Startup recovery already handles these at boot; the endpoint exists so
    # a long-running service does not have to restart, and every applied action is audited.
    repair_plan_ttl_seconds = 600
    repair_max_actions = 500
    repairable_kinds = (
        "interrupted_table_delete", "table_path_mismatch", "table_deletion_marker", "orphan_table_file",
        "catalogue_table_file_missing",
    )

    def repair_candidates():
        actions = []
        with connect(catalogue) as db:
            tables = {row["id"]: dict(row) for row in db.execute("SELECT id,path FROM tables")}
        for tombstone in sorted(table_directory.glob(".*.xl.deleting")):
            table_id = tombstone.name[1 : -len(".xl.deleting")]
            try:
                if str(UUID(table_id)) != table_id:
                    continue
            except ValueError:
                continue
            actions.append({
                "kind": "interrupted_table_delete",
                "table_id": table_id,
                "action": "restore" if table_id in tables else "finish",
            })
        for table_id, table in sorted(tables.items()):
            try:
                if str(UUID(table_id)) != table_id:
                    continue
            except ValueError:
                continue
            database = table_directory / f"{table_id}.xl"
            if not database.is_file():
                continue
            try:
                with connect(database) as db:
                    meta = db.execute("SELECT logical_path FROM __xl_meta").fetchone()
                    meta_columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
                    deleting = (
                        db.execute("SELECT deleting FROM __xl_meta").fetchone()[0]
                        if "deleting" in meta_columns
                        else 0
                    )
            except sqlite3.DatabaseError:
                continue
            if meta is None:
                continue
            if meta[0] != table["path"]:
                actions.append({
                    "kind": "table_path_mismatch",
                    "table_id": table_id,
                    "action": "reconcile_path",
                    "from": meta[0],
                    "to": table["path"],
                })
            if deleting:
                actions.append({
                    "kind": "table_deletion_marker",
                    "table_id": table_id,
                    "action": "clear_deleting_marker",
                })
        for entry in sorted(table_directory.glob("*.xl")):
            table_id = entry.stem
            try:
                if str(UUID(table_id)) != table_id:
                    continue
            except (ValueError, AttributeError):
                continue
            if table_id in tables:
                continue
            try:
                if time.time() - entry.stat().st_mtime < 300:
                    continue  # grace window for an in-flight table create
            except OSError:
                continue
            actions.append({
                "kind": "orphan_table_file",
                "table_id": table_id,
                "action": "quarantine",
            })
        quarantine_dir = table_directory / ".quarantine"
        if quarantine_dir.is_dir():
            quarantined = {}
            for entry in sorted(quarantine_dir.iterdir(), key=lambda item: item.name):
                if not entry.is_file() or entry.name.startswith("."):
                    continue
                queued_table_id = entry.name.split(".", 1)[0]
                try:
                    if str(UUID(queued_table_id)) != queued_table_id:
                        continue
                except (ValueError, AttributeError):
                    continue
                quarantined.setdefault(queued_table_id, []).append(entry)
            for queued_table_id, entries in sorted(quarantined.items()):
                database = table_directory / f"{queued_table_id}.xl"
                if queued_table_id not in tables or database.is_file():
                    continue
                for entry in entries:
                    try:
                        with sqlite3.connect(f"file:{entry}?mode=ro", uri=True) as check:
                            identity = check.execute("SELECT table_id FROM __xl_meta").fetchone()
                    except sqlite3.DatabaseError:
                        continue
                    if identity is None or identity[0] != queued_table_id:
                        continue
                    actions.append({
                        "kind": "catalogue_table_file_missing",
                        "table_id": queued_table_id,
                        "action": "restore_from_quarantine",
                        "file": entry.name,
                    })
                    break
        actions.sort(key=lambda item: (item["kind"], item["table_id"], item["action"]))
        return actions

    def repair_counts(actions):
        by_kind = {kind: 0 for kind in repairable_kinds}
        for action in actions:
            by_kind[action["kind"]] += 1
        return {"actions": len(actions), "by_kind": by_kind}

    def apply_repair_actions(actor, actions):
        applied = []
        for action in actions:
            kind, table_id = action["kind"], action["table_id"]
            result = "skipped"
            if kind == "interrupted_table_delete":
                with table_lock(table_id):
                    tombstone = table_directory / f".{table_id}.xl.deleting"
                    target = table_directory / f"{table_id}.xl"
                    if not tombstone.exists():
                        result = "skipped-missing-tombstone"
                    else:
                        with connect(catalogue) as db:
                            in_catalogue = (
                                db.execute("SELECT 1 FROM tables WHERE id=?", (table_id,)).fetchone() is not None
                            )
                        if in_catalogue:
                            if not target.exists():
                                os.replace(tombstone, target)
                            else:
                                tombstone.unlink(missing_ok=True)
                            if target.is_file():
                                with transaction(target) as db:
                                    columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
                                    if "deleting" in columns:
                                        db.execute("UPDATE __xl_meta SET deleting=0")
                            result = "restored"
                        else:
                            cleanup_table_uploads(table_id)
                            tombstone.unlink(missing_ok=True)
                            result = "finished"
            elif kind == "table_path_mismatch":
                database = table_directory / f"{table_id}.xl"
                if not database.is_file():
                    result = "skipped-missing-file"
                else:
                    with table_lock(table_id), transaction(database) as db:
                        meta = db.execute("SELECT logical_path FROM __xl_meta").fetchone()
                        if meta is not None and meta[0] == action["to"]:
                            result = "already-reconciled"
                        else:
                            db.execute(
                                "UPDATE __xl_meta SET logical_path=?,table_revision=table_revision+1,updated_at=?",
                                (action["to"], now()),
                            )
                            result = "reconciled"
            elif kind == "orphan_table_file":
                source = table_directory / f"{table_id}.xl"
                quarantine = table_directory / ".quarantine"
                if not source.is_file():
                    result = "skipped-missing-file"
                else:
                    quarantine.mkdir(mode=0o700, exist_ok=True)
                    destination = quarantine / f"{table_id}.xl"
                    if destination.exists():
                        destination = quarantine / f"{table_id}.{uuid4().hex}.xl"
                    os.replace(source, destination)
                    for suffix in ("-wal", "-shm", "-journal"):
                        sidecar = source.with_name(source.name + suffix)
                        if sidecar.exists():
                            os.replace(sidecar, destination.with_name(destination.name + suffix))
                    result = "quarantined"
            elif kind == "catalogue_table_file_missing":
                database = table_directory / f"{table_id}.xl"
                quarantine_dir = table_directory / ".quarantine"
                source = quarantine_dir / action["file"]
                with table_lock(table_id):
                    with connect(catalogue) as db:
                        in_catalogue = (
                            db.execute("SELECT 1 FROM tables WHERE id=?", (table_id,)).fetchone() is not None
                        )
                    if not in_catalogue:
                        result = "skipped-no-catalogue-row"
                    elif database.exists():
                        result = "skipped-file-present"
                    elif not source.is_file():
                        result = "skipped-missing-quarantine"
                    else:
                        try:
                            with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as check:
                                identity = check.execute("SELECT table_id FROM __xl_meta").fetchone()
                        except sqlite3.DatabaseError:
                            identity = None
                        if identity is None or identity[0] != table_id:
                            result = "skipped-identity-mismatch"
                        else:
                            os.replace(source, database)
                            for suffix in ("-wal", "-shm", "-journal"):
                                sidecar_source = source.with_name(source.name + suffix)
                                if sidecar_source.exists():
                                    os.replace(
                                        sidecar_source, database.with_name(database.name + suffix)
                                    )
                            result = "restored-from-quarantine"
            elif kind == "table_deletion_marker":
                database = table_directory / f"{table_id}.xl"
                if not database.is_file():
                    result = "skipped-missing-file"
                else:
                    with table_lock(table_id), transaction(database) as db:
                        columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
                        if "deleting" not in columns:
                            result = "no-marker"
                        else:
                            db.execute("UPDATE __xl_meta SET deleting=0")
                            result = "cleared"
            applied.append({**action, "result": result})
            audit(actor, "maintenance.repaired", f"{kind}:{table_id}:{result}")
        return applied

    def table_info(tid):
        with connect(catalogue) as db:
            row = db.execute("SELECT * FROM tables WHERE id=?", (tid,)).fetchone()
        if row is None:
            raise APIError(404, "XL_TABLE_NOT_FOUND", "table not found")
        return dict(row)

    def file(tid):
        # tid is looked up before it is ever used in a filesystem path.
        table_info(tid)
        path = root / "tables" / f"{tid}.xl"
        if not path.is_file():
            raise APIError(503, "XL_TABLE_STORAGE_UNAVAILABLE", "table file is unavailable")
        return path

    @contextmanager
    def table_lock(tid, *, exclusive=True):
        """Stable cross-process lock path, retained across the table-file rename."""
        try:
            if str(UUID(tid)) != tid:
                raise ValueError
        except (ValueError, AttributeError):
            raise APIError(404, "XL_TABLE_NOT_FOUND", "table not found") from None
        lock_dir = root / ".table-locks"
        lock_dir.mkdir(mode=0o700, exist_ok=True)
        with (lock_dir / f"{tid}.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def table_transaction(tid):
        """Serialize table mutations across API workers and reject deleting tables."""
        with table_lock(tid), transaction(file(tid)) as db:
            meta_columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
            if "deleting" not in meta_columns:
                db.execute("ALTER TABLE __xl_meta ADD COLUMN deleting INTEGER NOT NULL DEFAULT 0")
            if db.execute("SELECT deleting FROM __xl_meta").fetchone()[0]:
                raise APIError(404, "XL_TABLE_NOT_FOUND", "table is being deleted")
            yield db

    @contextmanager
    def table_connect(tid):
        """Hold the stable table lock while opening and reading the database."""
        with table_lock(tid, exclusive=False), connect(file(tid)) as db:
            meta_columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
            if "deleting" in meta_columns and db.execute("SELECT deleting FROM __xl_meta").fetchone()[0]:
                raise APIError(404, "XL_TABLE_NOT_FOUND", "table is being deleted")
            yield db

    def remove_qdrant_points(tid, row_id=None):
        """Synchronously remove derived points before committing source changes."""
        cleanup = getattr(app.state, "qdrant_remove_points", None)
        if cleanup:
            cleanup(tid, row_id)

    def allowed(actor, table, action, row_id=None, db=None):
        if "xelibrary:admin" in actor.scopes:
            return True
        if db is None:
            with connect(catalogue) as connection:
                return allowed(actor, table, action, row_id, connection)
        resources = [("row", f"{table['id']}:{row_id}")] if row_id else []
        resources += [("table", table["id"])]
        path = table["path"]
        while True:
            resources.append(("path", path))
            if path == "/":
                break
            path = str(PurePosixPath(path).parent)
        for kind, rid in resources:
            acl = db.execute(
                "SELECT grants,explicit FROM acl WHERE resource_type=? AND resource_id=?", (kind, rid)
            ).fetchone()
            if acl and acl["explicit"]:
                grants = json.loads(acl[0])
                for principal in (actor.sub, *actor.ancestors):
                    if principal in grants:
                        return action in grants[principal] or action in grants.get("*", [])
                return action in grants.get("*", [])
        return table["owner"] in (actor.sub, *actor.ancestors)

    def table_permissions(actor, table, db=None):
        """Caller-visible table permissions; the account tree and ACL are evaluated server-side."""
        return {
            action: allowed(actor, table, action, db=db) for action in ("read", "write", "attach")
        }

    def path_write_allowed(actor, path, db=None):
        """Enforce only the destination path ACL chain; no explicit path ACL inherits table write."""
        if "xelibrary:admin" in actor.scopes:
            return True
        if db is None:
            with connect(catalogue) as connection:
                return path_write_allowed(actor, path, connection)
        current = path
        while True:
            acl = db.execute(
                "SELECT grants,explicit FROM acl WHERE resource_type='path' AND resource_id=?", (current,)
            ).fetchone()
            if acl and acl["explicit"]:
                grants = json.loads(acl[0])
                for principal in (actor.sub, *actor.ancestors):
                    if principal in grants:
                        return "write" in grants[principal] or "write" in grants.get("*", [])
                return "write" in grants.get("*", [])
            if current == "/":
                break
            current = str(PurePosixPath(current).parent)
        return True

    def request_acl(actor, table):
        """Load a table's ACL policies once and reuse them for this request's rows."""
        if "xelibrary:admin" in actor.scopes:
            return lambda action, row_id=None: True

        paths = []
        path = table["path"]
        while True:
            paths.append(path)
            if path == "/":
                break
            path = str(PurePosixPath(path).parent)

        path_marks = ",".join("?" for _ in paths)
        # Row ACL identifiers are encoded as ``<table UUID>:<row UUID>``.
        # A BINARY range keeps this prefix lookup on the composite
        # (resource_type, resource_id) primary-key index; LIKE would scan every
        # row ACL in the catalogue because SQLite cannot derive a useful range
        # from the bound pattern on this compound key.
        row_prefix = table["id"] + ":"
        row_prefix_end = table["id"] + ";"
        with connect(catalogue) as db:
            policies = db.execute(
                "SELECT resource_type,resource_id,grants,explicit FROM acl WHERE "
                "(resource_type='table' AND resource_id=?) OR "
                "(resource_type='row' AND resource_id>=? AND resource_id<?) OR "
                f"(resource_type='path' AND resource_id IN ({path_marks}))",
                (table["id"], row_prefix, row_prefix_end, *paths),
            ).fetchall()
        by_resource = {
            (policy["resource_type"], policy["resource_id"]): (
                bool(policy["explicit"]), json.loads(policy["grants"]) if policy["explicit"] else {}
            )
            for policy in policies
        }
        principals = (actor.sub, *actor.ancestors)

        def permits(action, row_id=None):
            resources = [("row", f"{table['id']}:{row_id}")] if row_id else []
            resources.append(("table", table["id"]))
            resources.extend(("path", path_id) for path_id in paths)
            for resource in resources:
                policy = by_resource.get(resource)
                if policy and policy[0]:
                    grants = policy[1]
                    for principal in principals:
                        if principal in grants:
                            return action in grants[principal] or action in grants.get("*", [])
                    return action in grants.get("*", [])
            return table["owner"] in principals

        return permits

    def authorize_request(actor, tid, action):
        table = table_info(tid)
        permits = request_acl(actor, table)
        if not permits(action):
            raise APIError(403, "XL_FORBIDDEN", "resource permission denied")
        return table, permits

    def authorize(actor, tid, action, row_id=None):
        table = table_info(tid)
        if not allowed(actor, table, action, row_id):
            raise APIError(403, "XL_FORBIDDEN", "resource permission denied")
        return table

    def metadata(db):
        result = dict(db.execute("SELECT * FROM __xl_meta").fetchone())
        result.pop("deleting", None)
        return result

    def columns(db):
        return [json.loads(x[0]) for x in db.execute("SELECT definition FROM __xl_schema ORDER BY position")]

    def validate_values(schema, values, partial=False):
        known = {c["name"] for c in schema}
        if set(values) - known:
            raise APIError(422, "UNKNOWN_COLUMN", "unknown user field")
        result = dict(values)
        for col in schema:
            name, kind = col["name"], col["type"]
            if name not in result:
                if partial:
                    continue
                result[name] = col["default_value"]
            value = result[name]
            if value is None:
                if not col["nullable"]:
                    raise APIError(422, "NULL_NOT_ALLOWED", f"{name} is required")
                continue
            valid = {
                "string": isinstance(value, str),
                "text": isinstance(value, str),
                "int64": type(value) is int and -(2**63) <= value < 2**63,
                "float64": type(value) in (int, float) and math.isfinite(value),
                "boolean": type(value) is bool,
                "json": isinstance(value, (dict, list)),
                "attachment_list": isinstance(value, list),
                "date": isinstance(value, str),
                "datetime": isinstance(value, str),
                "enum": isinstance(value, str),
                "reference": isinstance(value, str),
                "theorem_ref": isinstance(value, str),
            }[kind]
            if not valid:
                raise APIError(422, "COLUMN_TYPE_ERROR", f"invalid type for {name}")
            if kind in ("date", "datetime"):
                try:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
                except ValueError:
                    raise APIError(422, "COLUMN_TYPE_ERROR", f"invalid date for {name}") from None
        return result

    def decode(row):
        return {
            "_row_id": row["_row_id"],
            "_revision": row["_revision"],
            "_created_at": row["_created_at"],
            "_updated_at": row["_updated_at"],
            "_deleted_at": row["_deleted_at"],
            "values": json.loads(row["values_json"]),
        }

    def load_row(db, rid, include_deleted=False):
        row = db.execute("SELECT * FROM data WHERE _row_id=?", (rid,)).fetchone()
        if row is None or (row["_deleted_at"] and not include_deleted):
            raise APIError(404, "XL_ROW_NOT_FOUND", "row not found")
        return decode(row)

    def change(db, row, operation):
        db.execute(
            "INSERT INTO __xl_changes(row_id,revision,operation,snapshot,created_at) VALUES(?,?,?,?,?)",
            (row["_row_id"], row["_revision"], operation, json.dumps(row), now()),
        )
        db.execute("UPDATE __xl_meta SET table_revision=table_revision+1,updated_at=?", (now(),))

    @app.post("/api/v1/tables", status_code=201)
    def create_table(body: TableCreate, request: Request, actor=Depends(scope("xelibrary:write"))):
        path = clean_path(body.path)
        names = [c.name for c in body.columns]
        if len(set(names)) != len(names) or any(c.type not in TYPES for c in body.columns):
            raise APIError(422, "INVALID_SCHEMA", "duplicate field or unsupported type")
        # Validate defaults before creating files.
        validate_values(
            [c.model_dump() for c in body.columns],
            {c.name: c.default_value for c in body.columns if c.default_value is not None},
            True,
        )
        if not allowed(actor, {"id": "", "path": path, "owner": actor.sub}, "write"):
            raise APIError(403, "XL_FORBIDDEN", "path permission denied")
        tid, stamp = str(uuid4()), now()
        target = root / "tables" / f"{tid}.xl"
        operation_id = begin_operation("table.create", tid, {"path": path, "name": body.name})
        try:
            with transaction(catalogue) as catalog:
                catalog.execute("INSERT INTO tables VALUES(?,?,?,?)", (tid, path, body.name, actor.sub))
                with transaction(target) as db:
                    db.executescript("""
                    CREATE TABLE __xl_meta(format_version TEXT,table_id TEXT,table_revision INTEGER,
                      logical_path TEXT,created_at TEXT,updated_at TEXT,created_by TEXT,schema_revision INTEGER);
                    CREATE TABLE __xl_schema(column_id TEXT PRIMARY KEY,position INTEGER,definition TEXT);
                    CREATE TABLE data(_row_id TEXT PRIMARY KEY,_revision INTEGER,_created_at TEXT,
                      _updated_at TEXT,_deleted_at TEXT,values_json TEXT);
                    CREATE TABLE __xl_changes(sequence INTEGER PRIMARY KEY AUTOINCREMENT,row_id TEXT,
                      revision INTEGER,operation TEXT,snapshot TEXT,created_at TEXT,indexed_revision INTEGER DEFAULT 0);
                    CREATE TABLE __xl_attachments(attachment_id TEXT PRIMARY KEY,row_id TEXT,object_hash TEXT,
                      filename TEXT,media_type TEXT,size INTEGER,created_at TEXT,metadata_json TEXT);
                    CREATE TABLE __xl_stats(key TEXT PRIMARY KEY,value TEXT);
                    CREATE TABLE __xl_idempotency(key TEXT PRIMARY KEY,digest TEXT,row_id TEXT);
                    CREATE VIRTUAL TABLE __xl_fts USING fts5(row_id UNINDEXED,body, tokenize='unicode61');
                    CREATE VIRTUAL TABLE __xl_fts_cjk USING fts5(row_id UNINDEXED,body, tokenize='unicode61');
                    """)
                    db.execute(
                        "INSERT INTO __xl_meta VALUES(?,?,?,?,?,?,?,?)",
                        ("1.0", tid, 1, path, stamp, stamp, actor.sub, 1),
                    )
                    for pos, col in enumerate(body.columns):
                        db.execute(
                            "INSERT INTO __xl_schema VALUES(?,?,?)",
                            (str(uuid4()), pos, col.model_dump_json()),
                        )
        except Exception as exc:
            target.unlink(missing_ok=True)
            abort_operation(operation_id, exc)
            import sqlite3

            if isinstance(exc, sqlite3.IntegrityError):
                raise APIError(409, "TABLE_EXISTS", "table name already exists in path") from None
            raise
        finish_operation(operation_id)
        audit(actor, "table.created", tid)
        return JSONResponse(
            response(request, {"table_id": tid, "path": path, "name": body.name, "revision": 1}),
            status_code=201,
            headers={"ETag": etag(1)},
        )

    @app.get("/api/v1/tables")
    def list_tables(
        request: Request, limit: int = 100, cursor: str = "", actor=Depends(scope("xelibrary:read"))
    ):
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables WHERE id>? ORDER BY id", (cursor,))]
            visible = []
            for table in tables:
                if not allowed(actor, table, "read", db=db):
                    continue
                visible.append({**table, "permissions": table_permissions(actor, table, db)})
                if len(visible) > limit:
                    break
        return response(
            request, visible[:limit], next_cursor=visible[limit - 1]["id"] if len(visible) > limit else None
        )

    @app.get("/api/v1/tables/{tid}")
    @app.get("/api/v1/tables/{tid}/schema")
    def get_table(tid: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        table = authorize(actor, tid, "read")
        with table_connect(tid) as db:
            data = {
                **table,
                **metadata(db),
                "columns": columns(db),
                "permissions": table_permissions(actor, table),
            }
        return JSONResponse(response(request, data), headers={"ETag": etag(data["table_revision"])})

    @app.delete(
        "/api/v1/tables/{tid}",
        responses={
            403: {"description": "The caller does not have administrator scope."},
            404: {"description": "The table does not exist."},
            409: {"description": "The table is non-empty, busy, or has active work."},
            412: {"description": "If-Match is missing or does not match the current ETag."},
            503: {"description": "The table file is unavailable."},
        },
        openapi_extra={
            "description": (
                "Requires `xelibrary:admin` scope and the current table ETag. Deletes empty tables only; "
                "rows, attachments, vector references, active index jobs, and active uploads must be cleared first."
            ),
            "parameters": [
                {
                    "name": "If-Match",
                    "in": "header",
                    "required": True,
                    "schema": {"type": "string"},
                    "description": "Current table revision ETag.",
                }
            ],
        },
    )
    def delete_table(tid: str, request: Request, actor=Depends(scope("xelibrary:admin"))):
        with table_lock(tid):
            authorize(actor, tid, "write")
            target = file(tid)
            if not target.is_file():
                raise APIError(503, "XL_TABLE_STORAGE_UNAVAILABLE", "table file is unavailable")

            # Deletion is deliberately limited to empty tables. Row history and vector facts
            # are removed through the existing row purge flow before the containing table.
            with transaction(file(tid)) as db:
                meta = metadata(db)
                meta_columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
                if "deleting" not in meta_columns:
                    db.execute("ALTER TABLE __xl_meta ADD COLUMN deleting INTEGER NOT NULL DEFAULT 0")
                precondition(request, meta["table_revision"])
                rows = db.execute("SELECT count(*) FROM data").fetchone()[0]
                attachments = db.execute("SELECT count(*) FROM __xl_attachments").fetchone()[0]
                vectors_table = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='__xl_vectors'"
                ).fetchone()
                vectors = db.execute("SELECT count(*) FROM __xl_vectors").fetchone()[0] if vectors_table else 0
            if attachments:
                raise APIError(
                    409,
                    "TABLE_HAS_ATTACHMENTS",
                    "unbind or purge every attachment reference before deleting the table",
                )
            if rows:
                raise APIError(409, "TABLE_NOT_EMPTY", "purge every row before deleting the table")
            if vectors:
                raise APIError(409, "TABLE_HAS_VECTORS", "remove derived vector references before deleting the table")

            with connect(catalogue) as db:
                jobs_exists = db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_jobs'"
                ).fetchone()
                jobs = [json.loads(row[0]) for row in db.execute("SELECT payload FROM index_jobs")] if jobs_exists else []
            if any(job.get("table_id") == tid and job.get("status") in ("queued", "running") for job in jobs):
                raise APIError(409, "TABLE_INDEX_ACTIVE", "wait for the table's index job to finish")
            if bulk_jobs.active_for_table(tid):
                raise APIError(409, "TABLE_BULK_ACTIVE", "wait for the table's bulk row job to finish")

            upload_database = root / "uploads.sqlite"
            if upload_database.exists():
                with connect(upload_database) as db:
                    active = db.execute(
                        "SELECT 1 FROM uploads WHERE table_id=? AND completed=0 AND cancelled=0 LIMIT 1", (tid,)
                    ).fetchone()
                if active:
                    raise APIError(409, "TABLE_UPLOAD_ACTIVE", "finish or cancel uploads before deleting the table")

            # The table lock prevents an index job from publishing new points
            # between cleanup and the canonical file/catalogue deletion.
            operation_id = begin_operation("table.delete", tid, {"path": table_info(tid)["path"]})
            try:
                remove_qdrant_points(tid)

                with connect(target) as db:
                    checkpoint = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                    if checkpoint and checkpoint[0]:
                        raise APIError(409, "TABLE_BUSY", "table has active readers or writers")
                with transaction(target) as db:
                    db.execute("UPDATE __xl_meta SET deleting=1")
                tombstone = table_directory / f".{tid}.xl.deleting"
                try:
                    os.replace(target, tombstone)
                except BaseException:
                    with transaction(target) as db:
                        db.execute("UPDATE __xl_meta SET deleting=0")
                    raise
                try:
                    with transaction(catalogue) as db:
                        current = db.execute("SELECT 1 FROM tables WHERE id=?", (tid,)).fetchone()
                        if current is None:
                            raise APIError(404, "XL_TABLE_NOT_FOUND", "table not found")
                        db.execute("DELETE FROM acl WHERE resource_type='table' AND resource_id=?", (tid,))
                        db.execute(
                            "DELETE FROM acl WHERE resource_type='row' AND resource_id LIKE ?",
                            (tid + ":%",),
                        )
                        db.execute("DELETE FROM tables WHERE id=?", (tid,))
                        db.execute(
                            "INSERT INTO audit VALUES(?,?,?,?,?)",
                            (str(uuid4()), actor.sub, "table.deleted", tid, now()),
                        )
                except BaseException:
                    if tombstone.exists() and not target.exists():
                        os.replace(tombstone, target)
                        with transaction(target) as db:
                            db.execute("UPDATE __xl_meta SET deleting=0")
                    raise

                cleanup_table_uploads(tid)
                try:
                    tombstone.unlink(missing_ok=True)
                except OSError:
                    logging.getLogger(__name__).exception("unable to remove deleted table tombstone")
                finish_operation(operation_id)
            except BaseException as exc:
                abort_operation(operation_id, exc)
                raise
            return response(request, {"table_id": tid, "deleted": True})

    @app.patch("/api/v1/tables/{tid}/schema")
    def update_schema(tid: str, body: SchemaWrite, request: Request, actor=Depends(scope("xelibrary:admin"))):
        authorize(actor, tid, "write")
        definitions = [c.model_dump() for c in body.columns]
        if len({c.name for c in body.columns}) != len(body.columns) or any(
            c.type not in TYPES for c in body.columns
        ):
            raise APIError(422, "INVALID_SCHEMA", "duplicate field or unsupported type")
        with table_transaction(tid) as db:
            meta = metadata(db)
            precondition(request, meta["table_revision"])
            if db.execute("SELECT count(*) FROM data").fetchone()[0] > 10000:
                raise APIError(
                    413, "SCHEMA_JOB_REQUIRED", "large schema migrations require a background worker"
                )
            rows = [decode(row) for row in db.execute("SELECT * FROM data")]
            names = {c.name for c in body.columns}
            for row in rows:
                values = validate_values(definitions, {k: v for k, v in row["values"].items() if k in names})
                db.execute(
                    "UPDATE data SET values_json=?,_revision=_revision+1,_updated_at=? WHERE _row_id=?",
                    (json.dumps(values), now(), row["_row_id"]),
                )
                current = load_row(db, row["_row_id"], True)
                change(db, current, "schema_update")
            db.execute("DELETE FROM __xl_schema")
            for pos, col in enumerate(body.columns):
                db.execute(
                    "INSERT INTO __xl_schema VALUES(?,?,?)", (str(uuid4()), pos, col.model_dump_json())
                )
            db.execute(
                "UPDATE __xl_meta SET schema_revision=schema_revision+1,table_revision=table_revision+1,updated_at=?",
                (now(),),
            )
            db.execute("DELETE FROM __xl_fts")
            ensure_cjk_fts(db)
            db.execute("DELETE FROM __xl_fts_cjk")
            for raw in db.execute("SELECT * FROM data WHERE _deleted_at IS NULL"):
                row = decode(raw)
                body = " ".join(str(v) for v in row["values"].values())
                db.execute(
                    "INSERT INTO __xl_fts VALUES(?,?)",
                    (row["_row_id"], body),
                )
                db.execute(
                    "INSERT INTO __xl_fts_cjk VALUES(?,?)",
                    (row["_row_id"], cjk_fts_body(row["values"].values())),
                )
            revision = metadata(db)["table_revision"]
        audit(actor, "schema.changed", tid)
        return JSONResponse(
            response(request, {"schema_revision": meta["schema_revision"] + 1, "table_revision": revision}),
            headers={"ETag": etag(revision)},
        )

    @app.post("/api/v1/tables/{tid}/move")
    def move(tid: str, body: Move, request: Request, actor=Depends(scope("xelibrary:write"))):
        table = authorize(actor, tid, "write")
        path = clean_path(body.path)
        if not path_write_allowed(actor, path):
            raise APIError(403, "XL_FORBIDDEN", "destination path permission denied")
        operation_id = begin_operation("table.move", tid, {"from": table["path"], "to": path})
        try:
            with table_transaction(tid) as db, transaction(catalogue) as catalog:
                meta = metadata(db)
                precondition(request, meta["table_revision"])
                remove_qdrant_points(tid)
                try:
                    catalog.execute("UPDATE tables SET path=? WHERE id=?", (path, tid))
                except Exception:
                    raise APIError(409, "TABLE_EXISTS", "destination table already exists") from None
                db.execute(
                    "UPDATE __xl_meta SET logical_path=?,table_revision=table_revision+1,updated_at=?",
                    (path, now()),
                )
            finish_operation(operation_id)
        except BaseException as exc:
            abort_operation(operation_id, exc)
            raise
        audit(actor, "table.moved", tid)
        return response(request, {"table_id": tid, "path": path})

    @app.post("/api/v1/tables/{tid}/rows", status_code=201)
    def create_row(tid: str, body: RowWrite, request: Request, actor=Depends(scope("xelibrary:write"))):
        authorize(actor, tid, "write")
        with table_transaction(tid) as db:
            key = request.headers.get("Idempotency-Key")
            digest = hashlib.sha256(body.model_dump_json().encode()).hexdigest()
            idempotency_key = f"{actor.sub}:{key}" if key else None
            if idempotency_key:
                old = db.execute(
                    "SELECT digest,row_id FROM __xl_idempotency WHERE key=?", (idempotency_key,)
                ).fetchone()
                if old:
                    if old["digest"] != digest:
                        raise APIError(409, "IDEMPOTENCY_CONFLICT", "key reused with different request")
                    authorize(actor, tid, "write", old["row_id"])
                    row = load_row(db, old["row_id"], True)
                    return JSONResponse(
                        response(request, row), status_code=201, headers={"ETag": etag(row["_revision"])}
                    )
            values = validate_values(columns(db), body.values)
            rid, stamp = str(uuid4()), now()
            db.execute(
                "INSERT INTO data VALUES(?,?,?,?,?,?)", (rid, 1, stamp, stamp, None, json.dumps(values))
            )
            row = load_row(db, rid)
            if idempotency_key:
                db.execute("INSERT INTO __xl_idempotency VALUES(?,?,?)", (idempotency_key, digest, rid))
            change(db, row, "create")
            db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(str(x) for x in values.values())))
            index_cjk_row(db, rid, values.values())
        return JSONResponse(response(request, row), status_code=201, headers={"ETag": etag(1)})

    @app.get("/api/v1/tables/{tid}/rows")
    def list_rows(
        tid: str,
        request: Request,
        limit: int = 100,
        cursor: str = "",
        include_deleted: bool = False,
        sort_by: str | None = Query(default=None, description="Optional schema or system field for table-wide ordering."),
        direction: str | None = Query(default=None, description="Sort direction: asc or desc; defaults to asc when sort_by is set."),
        actor=Depends(scope("xelibrary:read")),
    ):
        """List visible rows; sorted cursors are bound to the table, ordering, and deleted-row filter."""
        table, permits = authorize_request(actor, tid, "read")
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        with table_connect(tid) as db:
            if sort_by is None:
                if direction is not None:
                    raise APIError(422, "INVALID_SORT", "direction requires sort_by")
                if cursor.startswith(SORT_CURSOR_PREFIX):
                    raise APIError(422, "INVALID_CURSOR", "cursor does not match the requested sort")
                query = "SELECT * FROM data WHERE (? OR _deleted_at IS NULL) AND _row_id>? ORDER BY _row_id"
                raw_rows = db.execute(query, (include_deleted, cursor))
                rows = []
                for raw in raw_rows:
                    if permits("read", raw["_row_id"]):
                        rows.append(decode(raw))
                        if len(rows) > limit:
                            break
                page = rows[:limit]
                next_cursor = page[-1]["_row_id"] if len(rows) > limit else None
                return response(request, page, next_cursor=next_cursor)

            direction = "asc" if direction is None else direction
            if direction not in {"asc", "desc"}:
                raise APIError(422, "INVALID_SORT", "direction must be asc or desc")
            if not sort_by:
                raise APIError(422, "INVALID_SORT", "sort_by must be a non-empty field name")

            system_sort_expressions = {
                "_row_id": "_row_id",
                "_revision": "_revision",
                "_created_at": "_created_at",
                "_updated_at": "_updated_at",
                "_deleted_at": "_deleted_at",
            }
            if sort_by in system_sort_expressions:
                sort_expression = system_sort_expressions[sort_by]
                sort_parameters = []
            else:
                if not any(column["name"] == sort_by for column in columns(db)):
                    raise APIError(422, "INVALID_SORT", "sort_by must name a schema or supported system field")
                # Schema field names are constrained by Column.name. Bind the JSON path
                # as data as well, so it can never become SQL syntax.
                sort_expression = "json_extract(values_json, ?)"
                sort_parameters = ['$.' + json.dumps(sort_by, ensure_ascii=False)]

            sort_cursor = None
            if cursor:
                sort_cursor = decode_rows_sort_cursor(cursor)
                expected = {
                    "v": 1,
                    "table_id": tid,
                    "sort_by": sort_by,
                    "direction": direction,
                    "include_deleted": include_deleted,
                }
                if any(sort_cursor.get(key) != value for key, value in expected.items()):
                    raise APIError(422, "INVALID_CURSOR", "cursor does not match the requested table, sort, or filters")
                after_row_id = sort_cursor.get("after_row_id")
                after_value = sort_cursor.get("after_value")
                if not isinstance(after_row_id, str) or not after_row_id or not (
                    after_value is None or type(after_value) in {int, float, str}
                ):
                    raise APIError(422, "INVALID_CURSOR", "cursor is malformed")

            query = f"SELECT * FROM (SELECT data.*, {sort_expression} AS __sort_value FROM data) "
            query += "WHERE (? OR _deleted_at IS NULL)"
            parameters = [*sort_parameters, include_deleted]
            if sort_cursor:
                after_value = sort_cursor["after_value"]
                after_row_id = sort_cursor["after_row_id"]
                if after_value is None:
                    # SQLite sorts NULL first for ASC and last for DESC. An ASC
                    # cursor inside the NULL group must still admit every
                    # non-NULL row that follows it; only DESC NULLs are terminal.
                    if direction == "asc":
                        query += " AND ((__sort_value IS NULL AND _row_id>?) OR __sort_value IS NOT NULL)"
                    else:
                        query += " AND __sort_value IS NULL AND _row_id>?"
                    parameters.append(after_row_id)
                else:
                    operator = ">" if direction == "asc" else "<"
                    query += f" AND (__sort_value {operator} ? OR (__sort_value = ? AND _row_id>?)"
                    parameters.extend((after_value, after_value, after_row_id))
                    # SQLite puts NULL first for ASC and last for DESC. Only the
                    # latter can still appear after a non-null cursor value.
                    if direction == "desc":
                        query += " OR __sort_value IS NULL"
                    query += ")"
            query += f" ORDER BY __sort_value {direction.upper()}, _row_id ASC"
            rows = []
            for raw in db.execute(query, parameters):
                if permits("read", raw["_row_id"]):
                    rows.append((decode(raw), raw["__sort_value"]))
                    if len(rows) > limit:
                        break

            page = [row for row, _ in rows[:limit]]
            next_cursor = None
            if len(rows) > limit:
                last_row, last_value = rows[limit - 1]
                next_cursor = encode_rows_sort_cursor({
                    "v": 1,
                    "table_id": tid,
                    "sort_by": sort_by,
                    "direction": direction,
                    "include_deleted": include_deleted,
                    "after_value": last_value,
                    "after_row_id": last_row["_row_id"],
                })
        return response(
            request, page, next_cursor=next_cursor
        )

    @app.get("/api/v1/tables/{tid}/rows/{rid}")
    def get_row(
        tid: str,
        rid: str,
        request: Request,
        include_deleted: bool = False,
        actor=Depends(scope("xelibrary:read")),
    ):
        authorize(actor, tid, "read", rid)
        with table_connect(tid) as db:
            row = load_row(db, rid, include_deleted=include_deleted)
        return JSONResponse(response(request, row), headers={"ETag": etag(row["_revision"])})

    @app.patch("/api/v1/tables/{tid}/rows/{rid}")
    def patch_row(
        tid: str, rid: str, body: RowWrite, request: Request, actor=Depends(scope("xelibrary:write"))
    ):
        authorize(actor, tid, "write", rid)
        with table_transaction(tid) as db:
            row = load_row(db, rid)
            precondition(request, row["_revision"])
            remove_qdrant_points(tid, rid)
            values = {**row["values"], **validate_values(columns(db), body.values, True)}
            db.execute(
                "UPDATE data SET values_json=?,_revision=_revision+1,_updated_at=? WHERE _row_id=?",
                (json.dumps(values), now(), rid),
            )
            row = load_row(db, rid)
            change(db, row, "update")
            db.execute("DELETE FROM __xl_fts WHERE row_id=?", (rid,))
            db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(str(x) for x in values.values())))
            index_cjk_row(db, rid, values.values())
        return JSONResponse(response(request, row), headers={"ETag": etag(row["_revision"])})

    @app.delete("/api/v1/tables/{tid}/rows/{rid}")
    def delete_row(
        tid: str,
        rid: str,
        request: Request,
        purge: bool = False,
        actor=Depends(scope("xelibrary:write")),
    ):
        authorize(actor, tid, "write", rid)
        if purge:
            actor.require("xelibrary:admin")
            with table_transaction(tid) as db:
                row = load_row(db, rid, True)
                precondition(request, row["_revision"])
                remove_qdrant_points(tid, rid)
                for name, column in [
                    ("data", "_row_id"),
                    ("__xl_changes", "row_id"),
                    ("__xl_attachments", "row_id"),
                    ("__xl_idempotency", "row_id"),
                    ("__xl_fts", "row_id"),
                ]:
                    db.execute(f"DELETE FROM {name} WHERE {column}=?", (rid,))
                if cjk_fts_exists(db):
                    db.execute("DELETE FROM __xl_fts_cjk WHERE row_id=?", (rid,))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='__xl_vectors'").fetchone():
                    db.execute("DELETE FROM __xl_vectors WHERE row_id=?", (rid,))
                db.execute("UPDATE __xl_meta SET table_revision=table_revision+1,updated_at=?", (now(),))
            with transaction(catalogue) as db:
                db.execute("DELETE FROM acl WHERE resource_type='row' AND resource_id=?", (f"{tid}:{rid}",))
            audit(actor, "row.purged", f"{tid}:{rid}")
            return response(request, {"deleted": True, "purged": True})
        with table_transaction(tid) as db:
            row = load_row(db, rid)
            precondition(request, row["_revision"])
            remove_qdrant_points(tid, rid)
            db.execute("UPDATE data SET _deleted_at=?,_revision=_revision+1 WHERE _row_id=?", (now(), rid))
            change(db, load_row(db, rid, True), "delete")
            db.execute("DELETE FROM __xl_fts WHERE row_id=?", (rid,))
            if cjk_fts_exists(db):
                db.execute("DELETE FROM __xl_fts_cjk WHERE row_id=?", (rid,))
        audit(actor, "row.deleted", f"{tid}:{rid}")
        return response(request, {"deleted": True})

    def _bulk_thresholds():
        sync_limit = int(os.getenv("XELIBRARY_BULK_SYNC_LIMIT", "100"))
        max_rows = int(os.getenv("XELIBRARY_BULK_MAX_ROWS", "5000"))
        if sync_limit < 1 or max_rows < sync_limit:
            raise APIError(500, "BULK_CONFIG_INVALID", "bulk threshold configuration invalid")
        return sync_limit, max_rows

    def _bulk_guard(items, actor, tid, max_rows):
        if len(items) > max_rows:
            raise APIError(413, "BULK_TOO_LARGE", f"batch exceeds {max_rows} rows; split the batch")
        authorize(actor, tid, "write")

    def _bulk_apply(job_id, tid, kind, rows, actor):
        """Apply a persisted bulk job row by row (issue #83).

        Every item reuses the single-row semantics - schema validation, table and
        row ACL, per-item revision checks, change log, FTS maintenance and Qdrant
        cleanup - and commits in its own transaction, so partial success never
        rolls back rows that were already applied. The per-item result is written
        in the same table transaction as the mutation, so a crash between the
        table commit and the durable job record can be reconciled on restart
        instead of silently losing applied rows.
        """
        bulk_jobs.start(job_id)
        with table_transaction(tid) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS __xl_bulk_applied("
                "job_id TEXT,item_index INTEGER,result TEXT NOT NULL,failed INTEGER NOT NULL,"
                "PRIMARY KEY(job_id,item_index))"
            )

        def mark(db, index, result, failed=False):
            db.execute(
                "INSERT OR REPLACE INTO __xl_bulk_applied VALUES(?,?,?,?)",
                (job_id, index, json.dumps(result, sort_keys=True), 1 if failed else 0),
            )

        try:
            for index, item in enumerate(rows):
                failed = False
                try:
                    if kind == "insert":
                        authorize(actor, tid, "write")
                        with table_transaction(tid) as db:
                            values = validate_values(columns(db), item.values)
                            rid, stamp = str(uuid4()), now()
                            db.execute(
                                "INSERT INTO data VALUES(?,?,?,?,?,?)",
                                (rid, 1, stamp, stamp, None, json.dumps(values)),
                            )
                            row = load_row(db, rid)
                            change(db, row, "create")
                            db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(str(x) for x in values.values())))
                            index_cjk_row(db, rid, values.values())
                            result = {"index": index, "status": "created", "row_id": rid, "revision": 1}
                            mark(db, index, result)
                    elif kind == "update":
                        authorize(actor, tid, "write", item.row_id)
                        with table_transaction(tid) as db:
                            row = load_row(db, item.row_id)
                            if item.revision != row["_revision"]:
                                raise APIError(412, "STALE_REVISION", "revision mismatch")
                            remove_qdrant_points(tid, item.row_id)
                            values = {**row["values"], **validate_values(columns(db), item.values, True)}
                            db.execute(
                                "UPDATE data SET values_json=?,_revision=_revision+1,_updated_at=? WHERE _row_id=?",
                                (json.dumps(values), now(), item.row_id),
                            )
                            row = load_row(db, item.row_id)
                            change(db, row, "update")
                            db.execute("DELETE FROM __xl_fts WHERE row_id=?", (item.row_id,))
                            db.execute("INSERT INTO __xl_fts VALUES(?,?)", (item.row_id, " ".join(str(x) for x in values.values())))
                            index_cjk_row(db, item.row_id, values.values())
                            result = {"index": index, "status": "updated", "row_id": item.row_id, "revision": row["_revision"]}
                            mark(db, index, result)
                    else:
                        authorize(actor, tid, "write", item.row_id)
                        with table_transaction(tid) as db:
                            row = load_row(db, item.row_id)
                            if item.revision != row["_revision"]:
                                raise APIError(412, "STALE_REVISION", "revision mismatch")
                            remove_qdrant_points(tid, item.row_id)
                            db.execute(
                                "UPDATE data SET _deleted_at=?,_revision=_revision+1 WHERE _row_id=?",
                                (now(), item.row_id),
                            )
                            change(db, load_row(db, item.row_id, True), "delete")
                            db.execute("DELETE FROM __xl_fts WHERE row_id=?", (item.row_id,))
                            if cjk_fts_exists(db):
                                db.execute("DELETE FROM __xl_fts_cjk WHERE row_id=?", (item.row_id,))
                            result = {"index": index, "status": "deleted", "row_id": item.row_id}
                            mark(db, index, result)
                        audit(actor, "row.deleted", f"{tid}:{item.row_id}")
                except APIError as error:
                    failed = True
                    result = {
                        "index": index, "status": "failed",
                        "error": {"code": error.code, "message": error.message},
                    }
                except Exception:
                    logging.getLogger("xelibrary.bulk").exception("bulk item %s failed", index)
                    failed = True
                    result = {
                        "index": index, "status": "failed",
                        "error": {"code": "INTERNAL_ERROR", "message": "item failed; see service logs"},
                    }
                bulk_jobs.record(job_id, result, failed)
        except Exception:
            bulk_jobs.fail(job_id, "bulk worker crashed; partial results retained")
            raise

    def _bulk_marker_reader(tid, job_id):
        target = file(tid)
        if not target.is_file():
            return []
        with connect(target) as db:
            if not db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='__xl_bulk_applied'"
            ).fetchone():
                return []
            return [
                (row["item_index"], row["result"], row["failed"])
                for row in db.execute(
                    "SELECT item_index,result,failed FROM __xl_bulk_applied WHERE job_id=? ORDER BY item_index",
                    (job_id,),
                )
            ]

    bulk_jobs.recover(_bulk_marker_reader)

    def _duplicate_job_response(request, job):
        return JSONResponse(response(request, {
            "mode": job["status"], "job_id": job["id"], "duplicate": True,
            "status": job["status"], "total": job["total"],
            "done": job["done"], "failed": job["failed"],
        }))

    def _scoped_existing(kind, tid, payload, actor, batch_id):
        """Scoped idempotency lookup shared by bulk rows and import/export jobs."""
        if not batch_id:
            return None
        digest = payload_digest(kind, tid, payload)
        existing = bulk_jobs.find_batch(batch_id, actor.sub, tid, kind)
        if existing is not None and existing["payload_digest"] != digest:
            raise APIError(
                409, "BATCH_CONFLICT",
                "batch_id was already used for a different payload for this table and caller",
            )
        return existing

    def _create_scoped(kind, tid, payload, actor, batch_id, total=None):
        """Create a job, returning (job_id, winner) where winner is set on a race."""
        try:
            return bulk_jobs.create(tid, kind, payload, actor.sub, batch_id, total=total), None
        except sqlite3.IntegrityError:
            existing = bulk_jobs.find_batch(batch_id, actor.sub, tid, kind)
            if existing is None:
                raise
            if existing["payload_digest"] != payload_digest(kind, tid, payload):
                raise APIError(
                    409, "BATCH_CONFLICT",
                    "batch_id was already used for a different payload for this table and caller",
                ) from None
            return existing["id"], existing

    def _bulk_submit(kind, items, request, background, actor, tid, batch_id):
        sync_limit, max_rows = _bulk_thresholds()
        _bulk_guard(items, actor, tid, max_rows)
        payload = [item.model_dump() for item in items]
        digest = payload_digest(kind, tid, payload)
        existing = bulk_jobs.find_batch(batch_id, actor.sub, tid, kind) if batch_id else None
        if existing is not None:
            if existing["payload_digest"] != digest:
                raise APIError(
                    409, "BATCH_CONFLICT",
                    "batch_id was already used for a different payload for this table and caller",
                )
            return _duplicate_job_response(request, existing)
        try:
            job_id = bulk_jobs.create(tid, kind, payload, actor.sub, batch_id)
        except sqlite3.IntegrityError:
            # Concurrent identical requests race on the scoped unique index; the
            # loser re-reads the winning job instead of applying the batch twice.
            existing = bulk_jobs.find_batch(batch_id, actor.sub, tid, kind)
            if existing is None:
                raise
            if existing["payload_digest"] != digest:
                raise APIError(
                    409, "BATCH_CONFLICT",
                    "batch_id was already used for a different payload for this table and caller",
                ) from None
            return _duplicate_job_response(request, existing)
        if len(items) <= sync_limit:
            _bulk_apply(job_id, tid, kind, items, actor)
            job = bulk_jobs.get(job_id)
            return JSONResponse(response(request, {
                "mode": "sync", "job_id": job_id, "status": job["status"],
                "total": job["total"], "done": job["done"], "failed": job["failed"],
                "results": job["results"],
            }))
        background.add_task(_bulk_apply, job_id, tid, kind, items, actor)
        return JSONResponse(response(request, {
            "mode": "job", "job_id": job_id, "status": "queued", "total": len(items),
        }), status_code=202)

    @app.get("/api/v1/maintenance/consistency")
    def maintenance_consistency(request: Request, actor=Depends(scope("xelibrary:admin"))):
        """Read-only catalogue/file/attachment/object consistency scan (Issue #81)."""
        return response(request, scan_library(root))

    @app.post("/api/v1/maintenance/consistency/repairs")
    def maintenance_repairs(body: RepairRequest, request: Request, actor=Depends(scope("xelibrary:admin"))):
        """Dry-run-first repair plans for tombstones and catalogue/metadata path drift (Issue #81)."""
        actions = repair_candidates()
        if len(actions) > repair_max_actions:
            raise APIError(
                413,
                "REPAIR_TOO_LARGE",
                f"more than {repair_max_actions} repair actions; resolve in smaller batches",
            )
        if body.dry_run:
            plan_id = str(uuid4())
            plan_cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat().replace("+00:00", "Z")
            with transaction(catalogue) as db:
                db.execute("DELETE FROM repair_plans WHERE used=1 OR created_at<?", (plan_cutoff,))
                db.execute(
                    "INSERT INTO repair_plans VALUES(?,?,?,0)",
                    (plan_id, now(), json.dumps(actions, ensure_ascii=False)),
                )
            return response(request, {
                "dry_run": True,
                "plan_id": plan_id,
                "actions": actions,
                "counts": repair_counts(actions),
                "repairable_kinds": list(repairable_kinds),
            })
        if not body.plan_id:
            raise APIError(422, "REPAIR_PLAN_REQUIRED", "plan_id from a dry-run is required")
        with connect(catalogue) as db:
            plan = db.execute("SELECT * FROM repair_plans WHERE id=?", (body.plan_id,)).fetchone()
        if plan is None:
            raise APIError(404, "REPAIR_PLAN_NOT_FOUND", "repair plan not found")
        if plan["used"]:
            raise APIError(409, "REPAIR_PLAN_USED", "repair plan was already applied")
        created = datetime.fromisoformat(plan["created_at"].replace("Z", "+00:00"))
        if (datetime.now(timezone.utc) - created).total_seconds() > repair_plan_ttl_seconds:
            raise APIError(409, "REPAIR_PLAN_EXPIRED", "repair plan expired; run a new dry-run")
        if json.loads(plan["plan"]) != actions:
            raise APIError(
                409,
                "REPAIR_PLAN_STALE",
                "library state changed since the dry-run; run a new dry-run",
            )
        applied = apply_repair_actions(actor, actions)
        with transaction(catalogue) as db:
            db.execute("UPDATE repair_plans SET used=1 WHERE id=?", (body.plan_id,))
        return response(request, {
            "dry_run": False,
            "plan_id": body.plan_id,
            "applied": applied,
            "counts": repair_counts(actions),
        })

    @app.get("/api/v1/storage/telemetry")
    def storage_telemetry(request: Request, actor=Depends(scope("xelibrary:admin"))):
        tables: list[dict] = []
        referenced: set[str] = set()
        for database in sorted(table_directory.glob("*.xl")):
            try:
                with connect(database) as db:
                    changes = db.execute("SELECT COUNT(*) FROM __xl_changes").fetchone()[0]
                    attachments = db.execute(
                        "SELECT COUNT(*), COALESCE(SUM(size), 0) FROM __xl_attachments"
                    ).fetchone()
                    referenced.update(
                        row[0] for row in db.execute("SELECT DISTINCT object_hash FROM __xl_attachments WHERE object_hash IS NOT NULL")
                    )
            except (sqlite3.DatabaseError, OSError):
                continue
            tables.append({
                "table_id": database.stem,
                "changes_rows": changes,
                "attachment_refs": attachments[0],
                "attachment_bytes": attachments[1],
            })
        objects = root / "objects" / "sha256"
        total_objects = total_bytes = unreferenced_bytes = 0
        unreferenced = 0
        if objects.is_dir():
            for digest_dir in objects.iterdir():
                if not digest_dir.is_dir() or digest_dir.name.startswith("."):
                    continue
                for sub in digest_dir.iterdir():
                    if not sub.is_dir():
                        continue
                    for candidate in sub.iterdir():
                        if not candidate.is_file() or candidate.name.startswith("."):
                            continue
                        try:
                            size = candidate.stat().st_size
                        except OSError:
                            continue
                        total_objects += 1
                        total_bytes += size
                        if candidate.name not in referenced:
                            unreferenced += 1
                            unreferenced_bytes += size
        return response(request, {
            "tables": tables,
            "totals": {
                "tables": len(tables),
                "changes_rows": sum(item["changes_rows"] for item in tables),
                "attachment_refs": sum(item["attachment_refs"] for item in tables),
                "attachment_bytes": sum(item["attachment_bytes"] for item in tables),
            },
            "attachment_objects": {
                "total": total_objects,
                "referenced": total_objects - unreferenced,
                "unreferenced": unreferenced,
                "bytes_total": total_bytes,
                "bytes_unreferenced": unreferenced_bytes,
            },
        })

    @app.post("/api/v1/storage/attachment-gc")
    def attachment_gc(body: AttachmentGcRequest, request: Request, actor=Depends(scope("xelibrary:admin"))):
        objects = root / "objects" / "sha256"
        referenced: set[str] = set()
        for database in table_directory.glob("*.xl"):
            try:
                with connect(database) as db:
                    referenced.update(
                        row[0] for row in db.execute("SELECT DISTINCT object_hash FROM __xl_attachments WHERE object_hash IS NOT NULL")
                    )
            except (sqlite3.DatabaseError, OSError):
                continue
        cutoff = time.time() - body.grace_hours * 3600
        candidates: list[tuple[str, object, int]] = []
        if objects.is_dir():
            for digest_dir in sorted(objects.iterdir()):
                if not digest_dir.is_dir() or digest_dir.name.startswith("."):
                    continue
                for sub in sorted(digest_dir.iterdir()):
                    if not sub.is_dir():
                        continue
                    for candidate in sorted(sub.iterdir()):
                        if not candidate.is_file() or candidate.name.startswith("."):
                            continue
                        if candidate.name in referenced:
                            continue
                        try:
                            stat = candidate.stat()
                        except OSError:
                            continue
                        if stat.st_mtime >= cutoff:
                            continue
                        candidates.append((candidate.name, candidate, stat.st_size))
                        if len(candidates) >= body.limit:
                            break
                    if len(candidates) >= body.limit:
                        break
                if len(candidates) >= body.limit:
                    break
        token = "gc-" + hashlib.sha256(
            ("\n".join(sorted(digest for digest, _, _ in candidates)) + f":{body.grace_hours}:{body.limit}").encode()
        ).hexdigest()[:40]
        deleted, deleted_bytes = 0, 0
        if not body.dry_run:
            if not body.confirm or body.confirm != token:
                raise APIError(409, "GC_CONFIRMATION_REQUIRED", "re-run dry_run and pass its confirm token")
            for _, path, size in candidates:
                try:
                    path.unlink()
                except OSError:
                    continue
                deleted += 1
                deleted_bytes += size
        audit(actor, "storage.attachment_gc", f"dry_run={body.dry_run} candidates={len(candidates)} deleted={deleted}")
        return response(request, {
            "dry_run": body.dry_run,
            "candidates": [{"digest": digest, "bytes": size} for digest, _, size in candidates],
            "candidate_count": len(candidates),
            "candidate_bytes": sum(size for _, _, size in candidates),
            "deleted": deleted,
            "deleted_bytes": deleted_bytes,
            "grace_hours": body.grace_hours,
            "confirm": token,
        })

    @app.post("/api/v1/tables/{tid}/maintenance/prune-changes")
    def prune_changes(tid: str, body: PruneChangesRequest, request: Request, actor=Depends(scope("xelibrary:admin"))):
        authorize(actor, tid, "write")
        if bulk_jobs.active_for_table(tid):
            raise APIError(409, "TABLE_BULK_ACTIVE", "wait for the table's bulk row job to finish")
        with connect(catalogue) as db:
            jobs_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_jobs'"
            ).fetchone()
            jobs = [json.loads(row[0]) for row in db.execute("SELECT payload FROM index_jobs")] if jobs_exists else []
        if any(job.get("table_id") == tid and job.get("status") in ("queued", "running") for job in jobs):
            raise APIError(409, "TABLE_INDEX_ACTIVE", "wait for the table's index job to finish")
        cutoff = (datetime.now(timezone.utc) - timedelta(days=body.older_than_days)).isoformat()
        with table_transaction(tid) as db:
            meta_columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
            if "deleting" in meta_columns and db.execute("SELECT deleting FROM __xl_meta").fetchone()[0]:
                raise APIError(409, "TABLE_DELETING", "table is being deleted")
            max_sequence = db.execute("SELECT COALESCE(MAX(sequence), 0) FROM __xl_changes").fetchone()[0]
            floor = max(1, max_sequence - body.keep_recent + 1)
            candidates = [
                row[0]
                for row in db.execute(
                    """
                    SELECT c.sequence FROM __xl_changes c
                    WHERE c.created_at < ? AND c.sequence < ?
                      AND c.revision < (SELECT MAX(revision) FROM __xl_changes WHERE row_id = c.row_id)
                    ORDER BY c.sequence LIMIT ?
                    """,
                    (cutoff, floor, body.limit),
                )
            ]
            deleted = 0
            if not body.dry_run and candidates:
                db.executemany("DELETE FROM __xl_changes WHERE sequence = ?", [(sequence,) for sequence in candidates])
                deleted = len(candidates)
        audit(actor, "table.prune_changes", f"{tid} dry_run={body.dry_run} candidates={len(candidates)} deleted={deleted}")
        return response(request, {
            "dry_run": body.dry_run,
            "candidates": len(candidates),
            "deleted": deleted,
            "older_than_days": body.older_than_days,
            "keep_recent": body.keep_recent,
            "limit": body.limit,
        })

    @app.post("/api/v1/tables/{tid}/rows:bulkInsert")
    def bulk_insert(tid: str, body: BulkInsert, request: Request, background: BackgroundTasks, actor=Depends(scope("xelibrary:write"))):
        return _bulk_submit("insert", body.rows, request, background, actor, tid, body.batch_id)

    @app.post("/api/v1/tables/{tid}/rows:bulkUpdate")
    def bulk_update(tid: str, body: BulkUpdate, request: Request, background: BackgroundTasks, actor=Depends(scope("xelibrary:write"))):
        return _bulk_submit("update", body.rows, request, background, actor, tid, body.batch_id)

    @app.post("/api/v1/tables/{tid}/rows:bulkDelete")
    def bulk_delete(tid: str, body: BulkDelete, request: Request, background: BackgroundTasks, actor=Depends(scope("xelibrary:write"))):
        return _bulk_submit("delete", body.rows, request, background, actor, tid, body.batch_id)

    @app.post("/api/v1/tables/{tid}/rows:importBatch")
    def import_batch(tid: str, body: ImportBatch, request: Request, actor=Depends(scope("xelibrary:admin"))):
        """Atomically insert a bounded batch of rows keyed by caller-supplied idempotency keys."""
        authorize(actor, tid, "write")
        imported = existing = 0
        with table_transaction(tid) as db:
            definitions = columns(db)
            for item in body.rows:
                values = validate_values(definitions, item.values)
                digest = hashlib.sha256(
                    json.dumps(values, sort_keys=True, ensure_ascii=False).encode("utf-8")
                ).hexdigest()
                idempotency_key = f"{actor.sub}:import:{item.key}"
                previous = db.execute(
                    "SELECT digest FROM __xl_idempotency WHERE key=?", (idempotency_key,)
                ).fetchone()
                if previous is not None:
                    if previous["digest"] != digest:
                        raise APIError(
                            409,
                            "IDEMPOTENCY_CONFLICT",
                            f"import key {item.key!r} was already used with different values",
                        )
                    existing += 1
                    continue
                rid, stamp = str(uuid4()), now()
                db.execute(
                    "INSERT INTO data VALUES(?,?,?,?,?,?)",
                    (rid, 1, stamp, stamp, None, json.dumps(values, ensure_ascii=False)),
                )
                row = load_row(db, rid)
                change(db, row, "create")
                db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(str(x) for x in values.values())))
                index_cjk_row(db, rid, values.values())
                db.execute("INSERT INTO __xl_idempotency VALUES(?,?,?)", (idempotency_key, digest, rid))
                imported += 1
        audit(actor, "rows.import_batch", f"{tid}:imported={imported}:existing={existing}")
        return response(
            request,
            {"table_id": tid, "imported": imported, "existing": existing, "total": len(body.rows)},
        )

    @app.get("/api/v1/tables/{tid}/rows:bulk/{job_id}")
    def bulk_job_status(tid: str, job_id: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        authorize(actor, tid, "read")
        job = bulk_jobs.get(job_id)
        if job is None or job["table_id"] != tid:
            raise APIError(404, "JOB_NOT_FOUND", "bulk job not found")
        if job["owner"] != actor.sub and "xelibrary:admin" not in actor.scopes:
            raise APIError(403, "FORBIDDEN", "bulk job belongs to another caller")
        return response(request, {
            "job_id": job["id"], "table_id": job["table_id"], "kind": job["kind"],
            "batch_id": job["batch_id"], "status": job["status"], "total": job["total"],
            "done": job["done"], "failed": job["failed"], "results": job["results"],
            "error": job["error"], "created_at": job["created_at"], "updated_at": job["updated_at"],
        })

    def _import_export_guard(job, actor):
        if job["owner"] != actor.sub and "xelibrary:admin" not in actor.scopes:
            raise APIError(403, "FORBIDDEN", "job belongs to another caller")

    def _export_run(job_id, tid, fmt, actor, include_deleted):
        """Write an ACL-filtered snapshot (JSONL or CSV); only readable rows are included."""
        suffix = "jsonl" if fmt == "jsonl" else "csv"
        output = root / "exports" / f"{job_id}.{suffix}"
        output.parent.mkdir(parents=True, exist_ok=True)
        bulk_jobs.set_output(job_id, str(output))
        max_rows = int(os.getenv("XELIBRARY_EXPORT_MAX_ROWS", "100000"))
        done = 0
        try:
            table = authorize(actor, tid, "read")
            with table_connect(tid) as db, output.open("w", encoding="utf-8", newline="") as handle:
                columns_now = columns(db)
                writer = csv.writer(handle) if fmt == "csv" else None
                if fmt == "csv":
                    writer.writerow([c["name"] for c in columns_now])
                for raw in db.execute("SELECT * FROM data"):
                    if bulk_jobs.cancel_requested(job_id):
                        bulk_jobs.finish_cancelled(job_id)
                        return
                    row = decode(raw)
                    if row["_deleted_at"] and not include_deleted:
                        continue
                    if not allowed(actor, table, "read", row["_row_id"]):
                        continue
                    done += 1
                    if done > max_rows:
                        raise APIError(413, "EXPORT_TOO_LARGE", f"export exceeds {max_rows} rows")
                    if fmt == "jsonl":
                        handle.write(json.dumps(decode(raw)) + "\n")
                    else:
                        writer.writerow(
                            ["" if row["values"].get(c["name"]) is None else row["values"].get(c["name"])
                             for c in columns_now]
                        )
                    bulk_jobs.set_done(job_id, done)
            bulk_jobs.complete(job_id)
        except APIError as error:
            output.unlink(missing_ok=True)
            bulk_jobs.fail(job_id, f"{error.code}: {error.message}")
        except Exception:
            logging.getLogger("xelibrary.export").exception("export job %s crashed", job_id)
            output.unlink(missing_ok=True)
            bulk_jobs.fail(job_id, "export worker crashed")

    def _import_run(job_id, tid, fmt, actor, lines):
        done = 0
        try:
            if fmt == "csv":
                csv_rows = list(csv.reader(lines))
                header = csv_rows[0] if csv_rows else None
                entries = csv_rows[1:]
                with table_connect(tid) as schema_db:
                    schema = columns(schema_db)
                names = [c["name"] for c in schema]
                if header and set(header) - set(names):
                    bulk_jobs.fail(job_id, "UNKNOWN_COLUMN: unknown CSV header column")
                    return
            else:
                entries = lines
            for index, entry in enumerate(entries):
                if bulk_jobs.cancel_requested(job_id):
                    bulk_jobs.finish_cancelled(job_id)
                    return
                failed = False
                try:
                    if fmt == "csv":
                        values = {}
                        for column, raw in zip(schema, entry):
                            if column["name"] in header:
                                values[column["name"]] = csv_value_coercion(column, raw)
                    else:
                        values = json.loads(entry)
                        if not isinstance(values, dict):
                            raise APIError(422, "INVALID_ROW", "line is not a JSON object")
                        # exported rows carry the full snapshot; plain value objects also import
                        if isinstance(values.get("values"), dict):
                            values = values["values"]
                    authorize(actor, tid, "write")
                    with table_transaction(tid) as db:
                        validated = validate_values(columns(db), values)
                        rid, stamp = str(uuid4()), now()
                        db.execute(
                            "INSERT INTO data VALUES(?,?,?,?,?,?)",
                            (rid, 1, stamp, stamp, None, json.dumps(validated)),
                        )
                        row = load_row(db, rid)
                        change(db, row, "create")
                        db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(str(x) for x in validated.values())))
                        index_cjk_row(db, rid, validated.values())
                    result = {"index": index, "status": "created", "row_id": rid}
                except APIError as error:
                    failed = True
                    result = {"index": index, "status": "failed",
                              "error": {"code": error.code, "message": error.message}}
                except Exception:
                    logging.getLogger("xelibrary.import").exception("import line %s failed", index)
                    failed = True
                    result = {"index": index, "status": "failed",
                              "error": {"code": "INTERNAL_ERROR", "message": "line failed; see service logs"}}
                done += 1
                bulk_jobs.record(job_id, result, failed)
        except Exception:
            bulk_jobs.fail(job_id, "import worker crashed; partial results retained")
            raise

    @app.post("/api/v1/exports")
    def create_export(body: ExportRequest, request: Request, background: BackgroundTasks,
                      actor=Depends(scope("xelibrary:read"))):
        authorize(actor, body.table_id, "read")
        payload = {"format": body.format, "include_deleted": body.include_deleted}
        existing = _scoped_existing("export", body.table_id, payload, actor, body.batch_id)
        if existing is not None:
            return _duplicate_job_response(request, existing)
        job_id, winner = _create_scoped("export", body.table_id, payload, actor, body.batch_id)
        if winner is not None:
            return _duplicate_job_response(request, winner)
        background.add_task(_export_run, job_id, body.table_id, body.format, actor, body.include_deleted)
        return JSONResponse(response(request, {"job_id": job_id, "status": "queued"}), status_code=202)

    @app.get("/api/v1/exports/{job_id}")
    def export_status(job_id: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        job = bulk_jobs.get(job_id)
        if job is None or job["kind"] != "export":
            raise APIError(404, "JOB_NOT_FOUND", "export job not found")
        _import_export_guard(job, actor)
        return response(request, {"job_id": job["id"], "status": job["status"], "done": job["done"],
                                  "error": job["error"], "downloadable": job["status"] == "completed"})

    @app.get("/api/v1/exports/{job_id}/download")
    def export_download(job_id: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        job = bulk_jobs.get(job_id)
        if job is None or job["kind"] != "export":
            raise APIError(404, "JOB_NOT_FOUND", "export job not found")
        _import_export_guard(job, actor)
        if job["status"] != "completed" or not job["output"]:
            raise APIError(409, "EXPORT_NOT_READY", "export is not finished")
        return FileResponse(job["output"], media_type="application/x-ndjson",
                            filename=f"{job['table_id']}.jsonl")

    @app.post("/api/v1/imports")
    def create_import(body: ImportRequest, request: Request, background: BackgroundTasks,
                      actor=Depends(scope("xelibrary:write"))):
        authorize(actor, body.table_id, "write")
        max_bytes = int(os.getenv("XELIBRARY_IMPORT_MAX_BYTES", "2000000"))
        if len(body.content.encode()) > max_bytes:
            raise APIError(413, "IMPORT_TOO_LARGE", f"content exceeds {max_bytes} bytes")
        lines = [line for line in body.content.splitlines() if line.strip()]
        if not lines:
            raise APIError(422, "IMPORT_EMPTY", "no rows to import")
        # CSV: the first line is the header, so total counts data rows only.
        total = len(lines) - 1 if body.format == "csv" else len(lines)
        if total <= 0:
            raise APIError(422, "IMPORT_EMPTY", "no rows to import")
        payload = {"format": body.format, "lines": lines}
        existing = _scoped_existing("import", body.table_id, payload, actor, body.batch_id)
        if existing is not None:
            return _duplicate_job_response(request, existing)
        job_id, winner = _create_scoped("import", body.table_id, payload, actor, body.batch_id, total=total)
        if winner is not None:
            return _duplicate_job_response(request, winner)
        background.add_task(_import_run, job_id, body.table_id, body.format, actor, lines)
        return JSONResponse(response(request, {"job_id": job_id, "status": "queued", "total": len(lines)}), status_code=202)

    @app.get("/api/v1/imports/{job_id}")
    def import_status(job_id: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        job = bulk_jobs.get(job_id)
        if job is None or job["kind"] != "import":
            raise APIError(404, "JOB_NOT_FOUND", "import job not found")
        _import_export_guard(job, actor)
        return response(request, {"job_id": job["id"], "status": job["status"], "total": job["total"],
                                  "done": job["done"], "failed": job["failed"], "results": job["results"],
                                  "error": job["error"]})

    @app.post("/api/v1/imports/{job_id}/cancel")
    def cancel_import(job_id: str, request: Request, actor=Depends(scope("xelibrary:write"))):
        job = bulk_jobs.get(job_id)
        if job is None or job["kind"] != "import":
            raise APIError(404, "JOB_NOT_FOUND", "import job not found")
        _import_export_guard(job, actor)
        if not bulk_jobs.request_cancel(job_id):
            raise APIError(409, "JOB_FINISHED", "job already reached a terminal state")
        return response(request, {"job_id": job_id, "cancelled": True})

    @app.get("/api/v1/tables/{tid}/rows/{rid}/revisions")
    def revisions(tid: str, rid: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        authorize(actor, tid, "read", rid)
        with table_connect(tid) as db:
            load_row(db, rid, True)
            rows = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT snapshot FROM __xl_changes WHERE row_id=? ORDER BY revision", (rid,)
                )
            ]
        return response(request, rows)

    @app.get("/api/v1/tables/{tid}/rows/{rid}/revisions/{revision}")
    def revision(tid: str, rid: str, revision: int, request: Request, actor=Depends(scope("xelibrary:read"))):
        authorize(actor, tid, "read", rid)
        with table_connect(tid) as db:
            load_row(db, rid, True)
            old = db.execute(
                "SELECT snapshot FROM __xl_changes WHERE row_id=? AND revision=?", (rid, revision)
            ).fetchone()
        if old is None:
            raise APIError(404, "REVISION_NOT_FOUND", "revision not found")
        return response(request, json.loads(old[0]))

    @app.post("/api/v1/tables/{tid}/rows/{rid}/restore")
    def restore(tid: str, rid: str, body: Restore, request: Request, actor=Depends(scope("xelibrary:write"))):
        authorize(actor, tid, "write", rid)
        with table_transaction(tid) as db:
            current = load_row(db, rid, True)
            precondition(request, current["_revision"])
            remove_qdrant_points(tid, rid)
            old = db.execute(
                "SELECT snapshot FROM __xl_changes WHERE row_id=? AND revision=?", (rid, body.revision)
            ).fetchone()
            if old is None:
                raise APIError(404, "REVISION_NOT_FOUND", "revision not found")
            values = validate_values(columns(db), json.loads(old[0])["values"])
            db.execute(
                "UPDATE data SET values_json=?,_revision=_revision+1,_updated_at=?,"
                "_deleted_at=NULL WHERE _row_id=?",
                (json.dumps(values), now(), rid),
            )
            row = load_row(db, rid)
            change(db, row, "restore")
            db.execute("DELETE FROM __xl_fts WHERE row_id=?", (rid,))
            db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(str(v) for v in values.values())))
            index_cjk_row(db, rid, values.values())
        audit(actor, "row.restored", f"{tid}:{rid}")
        return JSONResponse(response(request, row), headers={"ETag": etag(row["_revision"])})

    def execute_query(body: QueryBody, request: Request, actor):
        plan = parse(body.expression)
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables ORDER BY id")]
        target = plan.offset + plan.limit
        matches = []
        for table in tables:
            if not path_matches(table["path"], plan.path):
                continue
            permits = request_acl(actor, table)
            if not permits("read"):
                continue
            with table_connect(table["id"]) as db:
                for raw in db.execute("SELECT * FROM data WHERE _deleted_at IS NULL ORDER BY _row_id"):
                    row = decode(raw)
                    if permits("read", row["_row_id"]) and evaluate(
                        plan.predicate, row["values"]
                    ):
                        matches.append({"table_id": table["id"], "path": table["path"], **row})
                        if plan.order is None and len(matches) >= target:
                            return response(request, matches[plan.offset:], approximate=False)
        if plan.order is not None:
            # Global ordering has to see the whole matching set before slicing.
            matches.sort(key=lambda item: sort_key(plan.order, item["values"]), reverse=plan.descending)
        return response(request, matches[plan.offset:target], approximate=False)

    @app.post("/api/v1/query")
    def query(body: QueryBody, request: Request, actor=Depends(scope("xelibrary:query"))):
        started = time.monotonic()
        try:
            result = execute_query(body, request, actor)
        except Exception:
            metrics.counter("xelibrary_query_errors_total", help="Query expressions rejected or failed.")
            raise
        finally:
            metrics.observe(
                "xelibrary_query_duration_seconds", time.monotonic() - started,
                help="Query expression execution duration.",
            )
        metrics.counter("xelibrary_query_total", help="Query expressions executed.")
        return result

    @app.post("/api/v1/query/validate")
    @app.post("/api/v1/query/explain")
    def explain(body: QueryBody, request: Request, actor=Depends(scope("xelibrary:query"))):
        plan = parse(body.expression)
        return response(
            request,
            {
                "path": plan.path,
                "ast": json_ast(plan.predicate),
                "limit": plan.limit,
                "offset": plan.offset,
                "order_by": json_ast(plan.order),
                "descending": plan.descending,
                "execution": "bounded AST interpreter",
                "approximate": False,
            },
        )

    @app.get(
        "/api/v1/acl/{resource_type}/{resource_id:path}",
        responses={200: {"headers": {"ETag": {"description": "Current ACL revision", "schema": {"type": "string"}}}}},
    )
    def get_acl(
        resource_type: str, resource_id: str, request: Request, actor=Depends(scope("xelibrary:admin"))
    ):
        if resource_type not in ("path", "table", "row"):
            raise APIError(422, "INVALID_RESOURCE", "unsupported ACL resource")
        with connect(catalogue) as db:
            row = db.execute(
                "SELECT grants,revision,explicit FROM acl WHERE resource_type=? AND resource_id=?",
                (resource_type, resource_id),
            ).fetchone()
        revision = row["revision"] if row else 0
        data = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "explicit": bool(row["explicit"]) if row else False,
            "grants": json.loads(row["grants"]) if row and row["explicit"] else {},
            "revision": revision,
        }
        return JSONResponse(response(request, data), headers={"ETag": etag(revision)})

    @app.get("/api/v1/acl")
    def list_acls(
        request: Request, limit: int = 100, cursor: str = "", actor=Depends(scope("xelibrary:admin"))
    ):
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        with connect(catalogue) as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT resource_type,resource_id,grants,revision FROM acl "
                    "WHERE explicit=1 AND resource_type || ':' || resource_id>? "
                    "ORDER BY resource_type,resource_id LIMIT ?",
                    (cursor, limit + 1),
                )
            ]
        entries = rows[:limit]
        for entry in entries:
            entry["explicit"] = True
            entry["grants"] = json.loads(entry["grants"])
        next_cursor = (
            entries[-1]["resource_type"] + ":" + entries[-1]["resource_id"]
            if len(rows) > limit
            else None
        )
        return response(request, entries, next_cursor=next_cursor)

    @app.get("/api/v1/audit")
    def audit_list(
        request: Request, limit: int = 100, cursor: str = "", actor=Depends(scope("xelibrary:admin"))
    ):
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        with connect(catalogue) as db:
            rows = [
                dict(r)
                for r in db.execute("SELECT * FROM audit WHERE id>? ORDER BY id LIMIT ?", (cursor, limit + 1))
            ]
        return response(
            request, rows[:limit], next_cursor=rows[limit - 1]["id"] if len(rows) > limit else None
        )

    @app.put(
        "/api/v1/acl/{resource_type}/{resource_id:path}",
        responses={200: {"headers": {"ETag": {"description": "Updated ACL revision", "schema": {"type": "string"}}}}},
    )
    def put_acl(
        resource_type: str,
        resource_id: str,
        body: ACLWrite,
        request: Request,
        if_match: str | None = Header(default=None, alias="If-Match"),
        actor=Depends(scope("xelibrary:admin")),
    ):
        if resource_type not in ("path", "table", "row"):
            raise APIError(422, "INVALID_RESOURCE", "unsupported ACL resource")
        if any(set(actions) - {"read", "write", "attach"} for actions in body.grants.values()):
            raise APIError(422, "INVALID_GRANTS", "invalid ACL action")
        with transaction(catalogue) as db:
            current = db.execute(
                "SELECT revision FROM acl WHERE resource_type=? AND resource_id=?", (resource_type, resource_id)
            ).fetchone()
            current_revision = current["revision"] if current else 0
            if if_match != etag(current_revision):
                raise APIError(412, "STALE_REVISION", "If-Match must equal the current ETag")
            revision = current_revision + 1
            db.execute(
                "INSERT INTO acl(resource_type,resource_id,grants,revision,explicit) VALUES(?,?,?,?,1) "
                "ON CONFLICT(resource_type,resource_id) DO UPDATE SET "
                "grants=excluded.grants,revision=excluded.revision,explicit=1",
                (resource_type, resource_id, json.dumps(body.grants), revision),
            )
        audit(actor, "acl.changed", f"{resource_type}:{resource_id}")
        result = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "explicit": True,
            "grants": body.grants,
            "revision": revision,
        }
        return JSONResponse(response(request, result), headers={"ETag": etag(revision)})

    @app.delete(
        "/api/v1/acl/{resource_type}/{resource_id:path}",
        responses={200: {"headers": {"ETag": {"description": "Updated ACL revision", "schema": {"type": "string"}}}}},
    )
    def clear_acl(
        resource_type: str,
        resource_id: str,
        request: Request,
        if_match: str | None = Header(default=None, alias="If-Match"),
        actor=Depends(scope("xelibrary:admin")),
    ):
        if resource_type not in ("path", "table", "row"):
            raise APIError(422, "INVALID_RESOURCE", "unsupported ACL resource")
        with transaction(catalogue) as db:
            current = db.execute(
                "SELECT revision FROM acl WHERE resource_type=? AND resource_id=?", (resource_type, resource_id)
            ).fetchone()
            current_revision = current["revision"] if current else 0
            if if_match != etag(current_revision):
                raise APIError(412, "STALE_REVISION", "If-Match must equal the current ETag")
            revision = current_revision + 1
            db.execute(
                "INSERT INTO acl(resource_type,resource_id,grants,revision,explicit) VALUES(?,?, '{}', ?,0) "
                "ON CONFLICT(resource_type,resource_id) DO UPDATE SET "
                "grants='{}',revision=excluded.revision,explicit=0",
                (resource_type, resource_id, revision),
            )
        audit(actor, "acl.inherited", f"{resource_type}:{resource_id}")
        result = {
            "resource_type": resource_type,
            "resource_id": resource_id,
            "explicit": False,
            "grants": {},
            "revision": revision,
        }
        return JSONResponse(response(request, result), headers={"ETag": etag(revision)})

    @app.post("/api/v1/attachments", status_code=201)
    async def upload(
        request: Request,
        table_id: str,
        row_id: str,
        upload: UploadFile,
        actor=Depends(scope("xelibrary:attach")),
    ):
        authorize(actor, table_id, "attach", row_id)
        with table_connect(table_id) as db:
            load_row(db, row_id)
        content = await upload.read(16 * 1024 * 1024 + 1)
        if len(content) > 16 * 1024 * 1024:
            raise APIError(413, "UPLOAD_TOO_LARGE", "16 MiB upload limit")
        digest, aid = hashlib.sha256(content).hexdigest(), str(uuid4())
        target = root / "objects" / "sha256" / digest[:2] / digest[2:4] / digest
        target.parent.mkdir(parents=True, exist_ok=True)
        # The object is complete before the attachment reference is committed.
        temporary = target.with_name(f".{aid}.tmp")
        temporary.write_bytes(content)
        temporary.replace(target)
        filename = re.sub(r"[^\w. -]", "_", PurePosixPath(upload.filename or "attachment").name)[:128]
        media = (
            "application/pdf"
            if content.startswith(b"%PDF-")
            else ("image/png" if content.startswith(b"\x89PNG\r\n\x1a\n") else "application/octet-stream")
        )
        with table_transaction(table_id) as db:
            load_row(db, row_id)
            db.execute(
                "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
                (aid, row_id, digest, filename, media, len(content), now(), "{}"),
            )
            db.execute("UPDATE __xl_meta SET table_revision=table_revision+1,updated_at=?", (now(),))
        audit(actor, "attachment.created", f"{table_id}:{row_id}:{aid}")
        return response(
            request, {"attachment_id": aid, "object_hash": digest, "size": len(content), "media_type": media}
        )

    def attachment_metadata(row):
        return {
            "attachment_id": row["attachment_id"],
            "row_id": row["row_id"],
            "filename": row["filename"],
            "media_type": row["media_type"],
            "size": row["size"],
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"]),
        }

    register_upload_routes(
        app,
        root=root,
        authorize=authorize,
        file=file,
        load_row=load_row,
        audit=audit,
        attachment_metadata=attachment_metadata,
        response=response,
        now=now,
        table_transaction=table_transaction,
        table_connect=table_connect,
    )
    recover_table_deletions()
    recover_lifecycle_operations()

    @app.get("/api/v1/tables/{tid}/attachments", response_model=AttachmentCandidatePage, description=(
        "List same-table attachment metadata for currently readable, live source rows. "
        "Requires xelibrary:read and table/row ACL. UUID keyset cursor is bound to table "
        "and normalized search, not authority; every page checks current identity/ACL. "
        "At most 500 references are examined per page; empty pages may have next_cursor. "
        "Search is a casefolded substring of filename or displayed source_label. "
        "No content, object hash, row values, preview or total count is returned."
    ))
    def list_table_attachments(
        tid: str,
        request: Request,
        limit: int = Query(default=25, ge=1, le=100),
        q: str = Query(default="", max_length=200),
        cursor: str = Query(default="", max_length=2048),
        actor=Depends(scope("xelibrary:read")),
    ):
        _, permits = authorize_request(actor, tid, "read")
        query = q.strip().casefold()
        after = ""
        if cursor:
            try:
                decoded = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                if not isinstance(decoded, dict) or set(decoded) != {"v", "table", "q", "after"}:
                    raise ValueError
                after = decoded["after"]
                if (type(decoded["v"]) is not int or decoded["v"] != 1 or decoded["table"] != tid or decoded["q"] != query
                        or not isinstance(after, str) or str(UUID(after)) != after):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError, binascii.Error):
                raise APIError(422, "INVALID_CURSOR", "invalid attachment list cursor") from None
        visible = []
        last = after
        more = False
        with table_connect(tid) as db:
            schema = columns(db)
            names = [c["name"] for c in schema if c["type"] in ("string", "text")]
            names = [name for name in ("title", "name") if name in names] + [
                name for name in names if name not in ("title", "name")
            ]
            rows = db.execute(
                "SELECT a.*,d._row_id AS source_row_id,d._deleted_at FROM __xl_attachments a LEFT JOIN data d ON d._row_id=a.row_id "
                "WHERE a.attachment_id>? "
                "ORDER BY a.attachment_id LIMIT 501", (after,)
            ).fetchall()
            # References can share a large source row; retain only its bounded label,
            # and never load its values before checking the current read permission.
            labels = {}
            for attachment in rows[:500]:
                if (attachment["source_row_id"] is not None and not attachment["_deleted_at"]
                        and permits("read", attachment["row_id"])):
                    source = attachment["row_id"]
                    if source not in labels:
                        row = db.execute(
                            "SELECT values_json FROM data WHERE _row_id=? "
                            "AND (_deleted_at IS NULL OR _deleted_at='')", (source,)
                        ).fetchone()
                        labels[source] = None
                        if row is not None and row["values_json"] is not None:
                            values = json.loads(row["values_json"])
                            labels[source] = next((values[name].strip()[:256] for name in names
                                                   if isinstance(values.get(name), str) and values[name].strip()), source)
                    label = labels[source]
                    if label is not None and (not query or query in attachment["filename"].casefold()
                                              or query in label.casefold()):
                        if len(visible) == limit:
                            more = True
                            break
                        # Only the readable row's label is exposed, never its other values.
                        visible.append({**attachment_metadata(attachment), "source_label": label})
                last = attachment["attachment_id"]
            more = more or len(rows) > 500
        next_cursor = None
        if more:
            next_cursor = base64.urlsafe_b64encode(json.dumps({
                "v": 1, "table": tid, "q": query, "after": last
            }, separators=(",", ":"), ensure_ascii=False).encode()).decode()
        return response(request, visible, next_cursor=next_cursor)

    @app.get("/api/v1/tables/{tid}/rows/{rid}/attachments")
    def list_attachments(tid: str, rid: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        authorize(actor, tid, "read", rid)
        with table_connect(tid) as db:
            load_row(db, rid)
            rows = db.execute(
                "SELECT * FROM __xl_attachments WHERE row_id=? ORDER BY created_at,attachment_id", (rid,)
            ).fetchall()
        return response(request, [attachment_metadata(row) for row in rows])

    @app.post("/api/v1/tables/{tid}/rows/{rid}/attachments", status_code=201)
    def bind_attachment(
        tid: str,
        rid: str,
        body: AttachmentBind,
        request: Request,
        actor=Depends(scope("xelibrary:attach")),
    ):
        authorize(actor, tid, "attach", rid)
        with table_transaction(tid) as db:
            load_row(db, rid)
            source = db.execute(
                "SELECT * FROM __xl_attachments WHERE attachment_id=?", (body.attachment_id,)
            ).fetchone()
            if source is None:
                raise APIError(404, "ATTACHMENT_NOT_FOUND", "attachment not found in this table")
            actor.require("xelibrary:read")
            authorize(actor, tid, "read", source["row_id"])
            load_row(db, source["row_id"])
            if source["row_id"] == rid:
                return JSONResponse(response(request, attachment_metadata(source)))
            aid = str(uuid4())
            db.execute(
                "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
                (
                    aid,
                    rid,
                    source["object_hash"],
                    source["filename"],
                    source["media_type"],
                    source["size"],
                    now(),
                    source["metadata_json"],
                ),
            )
            db.execute("UPDATE __xl_meta SET table_revision=table_revision+1,updated_at=?", (now(),))
            result = db.execute("SELECT * FROM __xl_attachments WHERE attachment_id=?", (aid,)).fetchone()
        audit(actor, "attachment.bound", f"{tid}:{rid}:{aid}")
        return JSONResponse(response(request, attachment_metadata(result)), status_code=201)

    @app.delete("/api/v1/tables/{tid}/rows/{rid}/attachments/{aid}")
    def unbind_attachment(
        tid: str,
        rid: str,
        aid: str,
        request: Request,
        actor=Depends(scope("xelibrary:attach")),
    ):
        authorize(actor, tid, "attach", rid)
        with table_transaction(tid) as db:
            load_row(db, rid)
            attachment = db.execute(
                "SELECT attachment_id FROM __xl_attachments WHERE attachment_id=? AND row_id=?", (aid, rid)
            ).fetchone()
            if attachment is None:
                raise APIError(404, "ATTACHMENT_NOT_FOUND", "attachment not found for row")
            db.execute("DELETE FROM __xl_attachments WHERE attachment_id=? AND row_id=?", (aid, rid))
            db.execute("UPDATE __xl_meta SET table_revision=table_revision+1,updated_at=?", (now(),))
        audit(actor, "attachment.unbound", f"{tid}:{rid}:{aid}")
        return response(request, {"attachment_id": aid, "unbound": True})

    @app.get("/api/v1/attachments/{aid}")
    def get_attachment(aid: str, table_id: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        table = authorize(actor, table_id, "read")
        with table_connect(table_id) as db:
            attachment = db.execute("SELECT * FROM __xl_attachments WHERE attachment_id=?", (aid,)).fetchone()
            if attachment is None:
                raise APIError(404, "ATTACHMENT_NOT_FOUND", "attachment not found")
            authorize(actor, table_id, "read", attachment["row_id"])
            load_row(db, attachment["row_id"])
        return response(request, {"table_id": table["id"], **attachment_metadata(attachment)})

    @app.get("/api/v1/attachments/{aid}/content")
    def download(aid: str, table_id: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        authorize(actor, table_id, "read")
        with table_connect(table_id) as db:
            attachment = db.execute("SELECT * FROM __xl_attachments WHERE attachment_id=?", (aid,)).fetchone()
            if attachment is None:
                raise APIError(404, "ATTACHMENT_NOT_FOUND", "attachment not found")
            authorize(actor, table_id, "read", attachment["row_id"])
            load_row(db, attachment["row_id"])
        digest = attachment["object_hash"]
        return FileResponse(
            root / "objects" / "sha256" / digest[:2] / digest[2:4] / digest,
            filename=attachment["filename"],
            media_type="application/octet-stream",
            headers={"X-Content-Type-Options": "nosniff"},
        )

    @app.get("/api/v1/attachments/{aid}/preview")
    def preview(aid: str, table_id: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        """Isolated, non-active preview for server-detected PNG attachments.

        The object is fully decoded and re-encoded with Pillow, so source chunks
        (EXIF/ICC/text) never reach the browser and a corrupt image fails the
        request instead of rendering partially. PDF previews need a rendering
        dependency decision (issue #152) and stay unsupported here.
        """
        authorize(actor, table_id, "read")
        with table_connect(table_id) as db:
            attachment = db.execute("SELECT * FROM __xl_attachments WHERE attachment_id=?", (aid,)).fetchone()
            if attachment is None:
                raise APIError(404, "ATTACHMENT_NOT_FOUND", "attachment not found")
            authorize(actor, table_id, "read", attachment["row_id"])
            load_row(db, attachment["row_id"])
        if attachment["media_type"] != "image/png":
            raise APIError(415, "PREVIEW_UNSUPPORTED", "only server-detected PNG attachments have an isolated preview")
        digest = attachment["object_hash"]
        source = root / "objects" / "sha256" / digest[:2] / digest[2:4] / digest
        if source.stat().st_size > 8 * 1024 * 1024:
            raise APIError(413, "PREVIEW_TOO_LARGE", "attachment exceeds the 8 MiB preview limit")
        from PIL import Image, UnidentifiedImageError

        try:
            with Image.open(source) as image:
                if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                    raise APIError(415, "PREVIEW_UNSUPPORTED", "attachment is not a single-frame PNG")
                # Bound decode memory using the header dimensions before load().
                if image.width > 4096 or image.height > 4096 or image.width * image.height > 8_000_000:
                    raise APIError(413, "PREVIEW_TOO_LARGE", "attachment exceeds the preview pixel limits")
                image.load()
                normalized = image.convert("RGBA")
                buffer = io.BytesIO()
                normalized.save(buffer, format="PNG")
        except APIError:
            raise
        except (UnidentifiedImageError, OSError, ValueError, ZeroDivisionError):
            raise APIError(422, "PREVIEW_DECODE_FAILED", "attachment could not be decoded as PNG") from None
        return Response(
            buffer.getvalue(),
            media_type="image/png",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @app.get("/api/v1/tables/{tid}/export")
    def export(tid: str, request: Request, actor=Depends(scope("xelibrary:admin"))):
        source = file(tid)
        snapshot = root / f"export-{uuid4()}.xl"
        with connect(source) as src, connect(snapshot) as dst:
            src.backup(dst)
        from starlette.background import BackgroundTask

        return FileResponse(
            snapshot,
            filename=f"{tid}.xl",
            media_type="application/octet-stream",
            background=BackgroundTask(snapshot.unlink, missing_ok=True),
        )

    @app.get("/api/v1/audit/events")
    def events(request: Request, actor=Depends(scope("xelibrary:admin"))):
        with connect(catalogue) as db:
            result = [dict(r) for r in db.execute("SELECT * FROM audit ORDER BY at DESC LIMIT 100")]
        return response(request, result)

    from .search import install
    from .services import Search
    from .theorems import install as install_theorems

    install(
        app,
        catalogue,
        file,
        authorize,
        allowed,
        request_acl,
        load_row,
        table_transaction,
        table_connect,
    )
    install_theorems(app, catalogue, allowed, audit)

    def collect_text(body, actor):
        # Literal term query: user input cannot inject FTS operators.
        terms = body.query.split()
        fts_query = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        if not fts_query:
            raise APIError(422, "EMPTY_QUERY", "query is empty")
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables ORDER BY id")]
        results_by_row = {}
        eligible_tables = []
        for table in tables:
            if not path_matches(table["path"], body.path):
                continue
            permits = request_acl(actor, table)
            if not permits("read"):
                continue
            eligible_tables.append((table, permits))
            with table_connect(table["id"]) as db:
                for hit in db.execute(
                    "SELECT row_id,bm25(__xl_fts) AS rank FROM __xl_fts WHERE __xl_fts MATCH ?", (fts_query,)
                ):
                    if not permits("read", hit["row_id"]):
                        continue
                    try:
                        row = load_row(db, hit["row_id"])
                    except APIError:
                        continue
                    if any(row["values"].get(k) != v for k, v in body.filters.items()):
                        continue
                    results_by_row[(table["id"], hit["row_id"])] = {
                        "table_id": table["id"],
                        "row_id": hit["row_id"],
                        "row_revision": row["_revision"],
                        "path": table["path"],
                        "score": -hit["rank"],
                        "score_type": "sqlite_fts_bm25",
                        "snippet": " ".join(str(v) for v in row["values"].values())[:240],
                        "approximate": False,
                    }

        partial = False
        if contains_cjk(body.query):
            # Older .xl files may not yet have the additive CJK index. Scan a
            # bounded prefix of all readable, path-matched active rows even when
            # the new index already found results; otherwise mixed-version
            # libraries silently miss legacy records.
            try:
                scan_budget = max(1, min(100_000, int(os.getenv("XELIBRARY_CJK_SCAN_ROWS", "5000"))))
            except ValueError:
                scan_budget = 5000
            remaining = scan_budget
            partial = False
            search_terms = [term.casefold() for term in terms]

            # Query the additive index for every eligible table independently
            # of the legacy-row scan budget. An indexed hit must not disappear
            # just because an earlier table exhausted that fallback budget.
            query = cjk_fts_query(body.query)
            for table, permits in eligible_tables:
                with table_connect(table["id"]) as db:
                    if not cjk_fts_exists(db):
                        continue
                    for hit in db.execute(
                        "SELECT row_id,bm25(__xl_fts_cjk) AS rank FROM __xl_fts_cjk WHERE __xl_fts_cjk MATCH ?",
                        (query,),
                    ):
                        key = (table["id"], hit["row_id"])
                        if key in results_by_row or not permits("read", hit["row_id"]):
                            continue
                        try:
                            row = load_row(db, hit["row_id"])
                        except APIError:
                            continue
                        if any(row["values"].get(k) != v for k, v in body.filters.items()):
                            continue
                        current_text = " ".join(str(v) for v in row["values"].values()).casefold()
                        if not all(term.casefold() in current_text for term in terms):
                            # A previous binary may have updated the source
                            # row while leaving this additive index stale.
                            continue
                        results_by_row[key] = {
                            "table_id": table["id"],
                            "row_id": hit["row_id"],
                            "row_revision": row["_revision"],
                            "path": table["path"],
                            "score": -hit["rank"],
                            "score_type": "sqlite_fts_cjk",
                            "snippet": " ".join(str(v) for v in row["values"].values())[:240],
                            "approximate": False,
                        }

            for table_index, (table, permits) in enumerate(eligible_tables):
                if remaining <= 0:
                    # One-row lookahead avoids an unbounded COUNT(*) over
                    # legacy tables while still distinguishing an exact budget
                    # boundary from an actually incomplete scan.
                    for later_table, _later_permits in eligible_tables[table_index:]:
                        with table_connect(later_table["id"]) as db:
                            if db.execute(
                                "SELECT 1 FROM data WHERE _deleted_at IS NULL LIMIT 1"
                            ).fetchone():
                                partial = True
                                break
                    break
                with table_connect(table["id"]) as db:
                    scan_rows = db.execute(
                        "SELECT * FROM data WHERE _deleted_at IS NULL ORDER BY _row_id LIMIT ?",
                        (remaining + 1,),
                    ).fetchall()
                    if len(scan_rows) > remaining:
                        partial = True
                    for raw in scan_rows[:remaining]:
                        row = decode(raw)
                        key = (table["id"], row["_row_id"])
                        if key in results_by_row or not permits("read", row["_row_id"]):
                            continue
                        if any(row["values"].get(k) != v for k, v in body.filters.items()):
                            continue
                        combined = " ".join(str(v) for v in row["values"].values()).casefold()
                        if all(term in combined for term in search_terms):
                            results_by_row[key] = {
                                "table_id": table["id"],
                                "row_id": row["_row_id"],
                                "row_revision": row["_revision"],
                                "path": table["path"],
                                "score": 0,
                                "score_type": "substring_scan",
                                "snippet": " ".join(str(v) for v in row["values"].values())[:240],
                                "approximate": True,
                            }
                    remaining -= min(remaining, len(scan_rows))
                    if partial:
                        break
        results = list(results_by_row.values())
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[: body.limit], {
            "partial": partial,
            "approximate": partial or any(hit["approximate"] for hit in results[: body.limit]),
        }

    @app.post(
        "/api/v1/search/text",
        response_model=SearchResponse,
        response_model_exclude_none=True,
        responses=SEARCH_ERRORS,
    )
    def text_search(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
        hits, meta = collect_text(body, actor)
        return response(request, hits, **meta)

    @app.post(
        "/api/v1/search/hybrid",
        response_model=SearchResponse,
        response_model_exclude_none=True,
        responses=SEARCH_ERRORS,
    )
    def hybrid(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
        # FTS and vector scores occupy different spaces; merge ranks only.
        quality = app.state.quality_pipeline
        deadline = quality.clock() + quality.time_budget if body.mode == "quality" else None
        semantic_hits, meta = app.state.semantic_collect(body, actor, request.state.request_id, deadline=deadline)
        text_body = body.model_copy(update={"limit": min(max(body.limit * 5, 100), 500)})
        text_hits, text_meta = collect_text(text_body, actor)
        hits = ranked_fusion([semantic_hits, text_hits], "hybrid_rrf")
        current = [hit for hit in hits if app.state.search_revalidate(hit, body, actor)]
        if deadline is not None:
            quality.check_deadline(deadline)
        meta["partial"] = bool(meta["partial"] or text_meta["partial"] or len(current) != len(hits))
        meta["approximate"] = bool(text_meta["approximate"] or any(hit["approximate"] for hit in current))
        return response(request, current[: body.limit], **meta)

    snapshot_ttl = 60
    snapshot_lock = threading.Lock()
    snapshot_cache = {"at": -1e9, "value": None, "error": False}

    def read_library_snapshot():
        rows = chunks = lag = 0
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables ORDER BY id")]
            backlog = sum(
                json.loads(payload).get("status") in ("queued", "running")
                for (payload,) in db.execute("SELECT payload FROM index_jobs")
            )

        for table in tables:
            with table_connect(table["id"]) as db:
                rows += db.execute("SELECT COUNT(*) FROM data WHERE _deleted_at IS NULL").fetchone()[0]
                try:
                    chunks += db.execute("SELECT COUNT(*) FROM __xl_vectors").fetchone()[0]
                except sqlite3.OperationalError as error:
                    if "no such table: __xl_vectors" not in str(error).lower():
                        raise
                lag += db.execute(
                    "SELECT COUNT(*) FROM data d WHERE d._deleted_at IS NULL AND d._revision >"
                    " COALESCE((SELECT MAX(c.indexed_revision) FROM __xl_changes c"
                    " WHERE c.row_id=d._row_id),0)"
                ).fetchone()[0]

        storage = 0
        for path in root.rglob("*"):
            try:
                info = path.stat()
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode):
                storage += info.st_size
        return {"rows": rows, "chunks": chunks, "lag": lag, "backlog": backlog, "storage": storage}

    def library_snapshot():
        # Serialize refreshes and retain both successful and failed results so a
        # burst of scrapes does not repeatedly traverse every table and file.
        with snapshot_lock:
            now = time.monotonic()
            if now - snapshot_cache["at"] < snapshot_ttl:
                if snapshot_cache["error"]:
                    raise RuntimeError("cached XeLibrary metrics snapshot is unavailable")
                return snapshot_cache["value"]
            try:
                value = read_library_snapshot()
            except Exception:
                snapshot_cache.update(at=now, value=None, error=True)
                raise
            snapshot_cache.update(at=now, value=value, error=False)
            return value

    def snapshot_success():
        try:
            library_snapshot()
        except Exception:
            return [(None, 0)]
        return [(None, 1)]

    metrics.gauge_provider(
        "xelibrary_snapshot_success", "Whether the most recent library metrics snapshot succeeded.",
        snapshot_success,
    )

    metrics.gauge_provider(
        "xelibrary_rows_total", "Non-deleted rows across all tables.",
        lambda: [(None, library_snapshot()["rows"])],
    )
    metrics.gauge_provider(
        "xelibrary_chunks_total", "Stored embedding chunks across all tables.",
        lambda: [(None, library_snapshot()["chunks"])],
    )
    metrics.gauge_provider(
        "xelibrary_index_lag", "Row revisions newer than their last indexed revision.",
        lambda: [(None, library_snapshot()["lag"])],
    )
    metrics.gauge_provider(
        "xelibrary_embedding_backlog", "Index jobs queued or running.",
        lambda: [(None, library_snapshot()["backlog"])],
    )
    metrics.gauge_provider(
        "xelibrary_storage_bytes", "Bytes stored under the XeLibrary data directory.",
        lambda: [(None, library_snapshot()["storage"])],
    )

    return app
