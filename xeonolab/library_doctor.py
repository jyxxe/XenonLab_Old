"""Read-only consistency scanner for the XeLibrary data root (Issue #81).

The scanner opens the catalogue, table databases and upload state through
SQLite read-only URI connections and never repairs, deletes or rewrites
anything. SQLite itself may create or refresh WAL shared-memory sidecars while
a read-only connection is open; the scanner issues no writes and leaves user
data byte-for-byte unchanged. It reports persistent inconsistencies between the
catalogue, the canonical ``<table_id>.xl`` files, attachment references, the
content-addressed object store and upload sessions so an administrator can
decide what to do.

This is the first #81 increment: detection only. The durable operation
journal/outbox and explicit repair actions are separate increments.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

MAX_ISSUES = 200

_HEX64 = re.compile(r"[0-9a-f]{64}")
_UUID_TEXT = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SHARD = re.compile(r"[0-9a-f]{2}")
_TABLE_SIDECARS = (".xl-wal", ".xl-shm", ".xl-journal")
_REQUIRED_TABLES = ("__xl_meta", "__xl_schema", "data", "__xl_changes", "__xl_attachments")


def _is_table_id(value):
    return isinstance(value, str) and _UUID_TEXT.fullmatch(value) is not None


def _read_connect(path):
    """Open an existing SQLite database read-only so a scan cannot mutate it."""
    database = sqlite3.connect(f"file:{quote(Path(path).as_posix())}?mode=ro", uri=True, timeout=30)
    database.row_factory = sqlite3.Row
    return database


def _catalogue_rows(catalogue):
    if not catalogue.is_file():
        return []
    database = _read_connect(catalogue)
    try:
        return [dict(row) for row in database.execute("SELECT id,path,name,owner FROM tables")]
    finally:
        database.close()


def _report(issues, truncated, counts, object_store):
    critical = sum(1 for issue in issues if issue["severity"] == "critical")
    return {
        "mode": "read_only",
        "scanned_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ok": critical == 0,
        "critical_issues": critical,
        "warning_issues": len(issues) - critical,
        "issues": issues,
        "issues_truncated": truncated,
        "counts": counts,
        "object_store": object_store,
    }


def scan_library(root, *, max_issues=MAX_ISSUES):
    """Return a bounded, read-only consistency report for one XeLibrary root."""
    root = Path(root)
    issues = []
    truncated = False
    counts = {
        "catalogue_tables": 0,
        "table_files": 0,
        "table_tombstones": 0,
        "quarantined_table_files": 0,
        "attachment_references": 0,
        "uploads": 0,
        "pending_operations": 0,
        "stale_vector_references": 0,
        "index_jobs": 0,
    }
    object_store = {
        "count": 0,
        "bytes": 0,
        "unreferenced": None,
        "unreferenced_bytes": None,
        "orphan_check_complete": False,
    }

    def add(kind, severity, *, table_id=None, detail=None):
        nonlocal truncated
        if len(issues) >= max_issues:
            truncated = True
            return
        issues.append({"kind": kind, "severity": severity, "table_id": table_id, "detail": detail})

    catalogue_path = root / "catalogue.sqlite"
    try:
        catalogue_before = _catalogue_rows(catalogue_path)
    except (sqlite3.DatabaseError, OSError):
        add("catalogue_unreadable", "critical", detail="catalogue database could not be read")
        return _report(issues, truncated, counts, object_store)

    tables_dir = root / "tables"
    table_file_ids = set()
    tombstones = set()
    if tables_dir.is_dir():
        for entry in sorted(tables_dir.iterdir(), key=lambda item: item.name):
            name = entry.name
            if entry.is_dir():
                if not name.startswith("."):
                    add("unexpected_table_entry", "warning", detail=f"unexpected directory {name!r} in tables/")
                continue
            if name.endswith(_TABLE_SIDECARS):
                continue
            if name.startswith(".") and name.endswith(".xl.deleting"):
                table_id = name[1 : -len(".xl.deleting")]
                if _is_table_id(table_id):
                    tombstones.add(table_id)
                else:
                    add("unexpected_table_entry", "warning", detail=f"unexpected tombstone {name!r}")
                continue
            if name.endswith(".xl"):
                table_id = name[: -len(".xl")]
                if _is_table_id(table_id):
                    table_file_ids.add(table_id)
                else:
                    add("unexpected_table_entry", "warning", detail=f"unexpected table file {name!r}")
                continue
            add("unexpected_table_entry", "warning", detail=f"unexpected table directory entry {name!r}")
    counts["table_files"] = len(table_file_ids)
    counts["table_tombstones"] = len(tombstones)
    quarantine = tables_dir / ".quarantine"
    if quarantine.is_dir():
        for entry in sorted(quarantine.iterdir(), key=lambda item: item.name):
            if entry.is_file() and not entry.name.startswith("."):
                counts["quarantined_table_files"] += 1
                add(
                    "quarantined_table_file",
                    "warning",
                    detail=(
                        f"quarantined table file {entry.name!r} is not visible to the catalogue; "
                        "restore it manually after review"
                    ),
                )

    try:
        catalogue_now = _catalogue_rows(catalogue_path)
    except (sqlite3.DatabaseError, OSError):
        catalogue_now = catalogue_before
        add("catalogue_changed_during_scan", "warning", detail="second catalogue read failed; using the first snapshot")
    catalogue = {}
    for row in catalogue_now:
        table_id = row.get("id")
        if isinstance(table_id, str):
            catalogue[table_id] = row
    known_ids = set(catalogue)
    known_ids.update(row["id"] for row in catalogue_before if isinstance(row.get("id"), str))

    try:
        operation_db = _read_connect(catalogue_path)
        try:
            journal = operation_db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='operations'"
            ).fetchone()
            pending_operations = (
                [
                    dict(row)
                    for row in operation_db.execute(
                        "SELECT id,kind,table_id,created_at FROM operations "
                        "WHERE state='pending' ORDER BY created_at"
                    )
                ]
                if journal
                else []
            )
        finally:
            operation_db.close()
    except (sqlite3.DatabaseError, OSError):
        pending_operations = []
        add("operation_journal_unreadable", "warning", detail="operation journal could not be read")
    counts["pending_operations"] = len(pending_operations)
    for operation in pending_operations:
        add(
            "incomplete_lifecycle_operation",
            "warning",
            table_id=operation.get("table_id"),
            detail=(
                f"{operation.get('kind')} intent {operation.get('id')} has been pending since "
                f"{operation.get('created_at')}"
            ),
        )

    for table_id in sorted(table_file_ids - known_ids):
        add(
            "orphan_table_file",
            "critical",
            table_id=table_id,
            detail="table file has no catalogue entry",
        )
    for table_id in sorted(tombstones):
        in_catalogue = table_id in catalogue
        details = (
            "deletion was interrupted before the catalogue commit; startup recovery restores the table"
            if in_catalogue
            else "catalogue deletion committed; startup recovery finalizes upload/cleanup and removes the marker"
        )
        add("interrupted_table_delete", "warning", table_id=table_id, detail=details)

    referenced = set()
    orphan_check_complete = True
    for table_id in sorted(catalogue):
        table = catalogue[table_id]
        counts["catalogue_tables"] += 1
        if not _is_table_id(table_id):
            add("invalid_table_id", "critical", detail=f"catalogue table id {table_id!r} is not a canonical UUID")
            orphan_check_complete = False
            continue
        database = tables_dir / f"{table_id}.xl"
        if not database.is_file():
            add(
                "catalogue_table_file_missing",
                "critical",
                table_id=table_id,
                detail="catalogue entry has no canonical table file",
            )
            orphan_check_complete = False
            continue
        try:
            db = _read_connect(database)
            try:
                present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                missing = [name for name in _REQUIRED_TABLES if name not in present]
                if missing:
                    add(
                        "table_schema_incomplete",
                        "critical",
                        table_id=table_id,
                        detail="missing required tables: " + ", ".join(missing),
                    )
                    orphan_check_complete = False
                    continue
                meta_rows = db.execute("SELECT * FROM __xl_meta").fetchall()
                if len(meta_rows) != 1:
                    add(
                        "table_metadata_invalid",
                        "critical",
                        table_id=table_id,
                        detail=f"expected exactly one metadata row, found {len(meta_rows)}",
                    )
                    meta = {}
                else:
                    meta = dict(meta_rows[0])
                if meta.get("table_id") != table_id:
                    add(
                        "table_identity_mismatch",
                        "critical",
                        table_id=table_id,
                        detail=f"table metadata identity is {meta.get('table_id')!r}",
                    )
                if meta.get("logical_path") != table.get("path"):
                    add(
                        "table_path_mismatch",
                        "warning",
                        table_id=table_id,
                        detail=(
                            "table metadata path "
                            f"{meta.get('logical_path')!r} differs from catalogue path {table.get('path')!r}"
                        ),
                    )
                if meta.get("deleting"):
                    add(
                        "table_deletion_marker",
                        "warning",
                        table_id=table_id,
                        detail="table is marked as deleting; startup recovery clears the marker for catalogue tables",
                    )
                table_revision = meta.get("table_revision")
                row_revision = db.execute("SELECT COALESCE(MAX(_revision), 0) FROM data").fetchone()[0]
                change_revision = db.execute(
                    "SELECT COALESCE(MAX(revision), 0) FROM __xl_changes"
                ).fetchone()[0]
                if isinstance(table_revision, int):
                    if row_revision > table_revision:
                        add(
                            "table_revision_lagging",
                            "critical",
                            table_id=table_id,
                            detail=f"max row revision {row_revision} exceeds table_revision {table_revision}",
                        )
                    if change_revision > table_revision:
                        add(
                            "change_revision_ahead",
                            "critical",
                            table_id=table_id,
                            detail=f"max change revision {change_revision} exceeds table_revision {table_revision}",
                        )
                if db.execute("SELECT COUNT(*) FROM __xl_schema").fetchone()[0] == 0:
                    add(
                        "table_schema_empty",
                        "critical",
                        table_id=table_id,
                        detail="table has no schema columns",
                    )
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='__xl_vectors'"
                ).fetchone():
                    stale_vectors = db.execute(
                        "SELECT COUNT(*) FROM __xl_vectors v LEFT JOIN data d ON d._row_id=v.row_id "
                        "WHERE d._row_id IS NULL OR d._deleted_at IS NOT NULL OR v.revision != d._revision"
                    ).fetchone()[0]
                    counts["stale_vector_references"] += stale_vectors
                    if stale_vectors:
                        add(
                            "stale_vector_reference",
                            "warning",
                            table_id=table_id,
                            detail=(
                                f"{stale_vectors} derived vector rows do not match a live row revision; "
                                "reindexing rebuilds them"
                            ),
                        )
                    invalid_indexed = db.execute(
                        "SELECT COUNT(*) FROM __xl_changes WHERE indexed_revision > revision"
                    ).fetchone()[0]
                    if invalid_indexed:
                        add(
                            "invalid_indexed_revision",
                            "warning",
                            table_id=table_id,
                            detail=f"{invalid_indexed} change rows have indexed_revision ahead of revision",
                        )
                for attachment in db.execute("SELECT attachment_id,row_id,object_hash FROM __xl_attachments"):
                    counts["attachment_references"] += 1
                    digest = attachment["object_hash"]
                    if not isinstance(digest, str) or _HEX64.fullmatch(digest) is None:
                        add(
                            "invalid_attachment_reference",
                            "critical",
                            table_id=table_id,
                            detail=f"attachment {attachment['attachment_id']!r} has an invalid object digest",
                        )
                    else:
                        referenced.add(digest)
                        object_path = root / "objects" / "sha256" / digest[:2] / digest[2:4] / digest
                        if not object_path.is_file():
                            add(
                                "missing_attachment_object",
                                "critical",
                                table_id=table_id,
                                detail=f"attachment {attachment['attachment_id']!r} object {digest} is missing",
                            )
                    if db.execute("SELECT 1 FROM data WHERE _row_id=?", (attachment["row_id"],)).fetchone() is None:
                        add(
                            "attachment_row_missing",
                            "warning",
                            table_id=table_id,
                            detail=(
                                f"attachment {attachment['attachment_id']!r} references missing row "
                                f"{attachment['row_id']!r}"
                            ),
                        )
            finally:
                db.close()
        except (sqlite3.DatabaseError, OSError):
            add(
                "table_file_unreadable",
                "critical",
                table_id=table_id,
                detail="table file could not be read as a SQLite database",
            )
            orphan_check_complete = False

    upload_ids = set()
    active_upload_ids = set()
    upload_path = root / "uploads.sqlite"
    if upload_path.is_file():
        try:
            db = _read_connect(upload_path)
            try:
                for row in db.execute("SELECT upload_id,table_id,completed,cancelled FROM uploads"):
                    counts["uploads"] += 1
                    upload_id = str(row["upload_id"])
                    upload_ids.add(upload_id)
                    if not row["completed"] and not row["cancelled"]:
                        active_upload_ids.add(upload_id)
                    if row["table_id"] not in catalogue:
                        add(
                            "upload_table_missing",
                            "warning",
                            detail=f"upload {upload_id!r} references unknown table {row['table_id']!r}",
                        )
            finally:
                db.close()
        except (sqlite3.DatabaseError, OSError):
            add("upload_state_unreadable", "warning", detail="upload state database could not be read")

    index_jobs = 0
    try:
        db = _read_connect(catalogue_path)
        try:
            if db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='index_jobs'"
            ).fetchone():
                for row in db.execute("SELECT payload FROM index_jobs"):
                    index_jobs += 1
                    try:
                        payload = json.loads(row[0])
                    except (TypeError, ValueError):
                        add("invalid_index_job", "warning", detail="index job payload is not valid JSON")
                        continue
                    if not isinstance(payload, dict):
                        add("invalid_index_job", "warning", detail="index job payload is not an object")
                        continue
                    table_id = payload.get("table_id")
                    if isinstance(table_id, str) and table_id not in catalogue:
                        add(
                            "index_job_table_missing",
                            "warning",
                            table_id=table_id,
                            detail="index job references a table that is not in the catalogue",
                        )
        finally:
            db.close()
    except (sqlite3.DatabaseError, OSError):
        add("index_job_state_unreadable", "warning", detail="index job state could not be read")
    counts["index_jobs"] = index_jobs

    staging = root / "objects" / ".uploads"
    if staging.is_dir():
        for entry in sorted(staging.iterdir(), key=lambda item: item.name):
            name = entry.name
            if name == ".locks":
                continue
            if entry.is_dir():
                if name not in upload_ids:
                    add("orphan_upload_staging", "warning", detail=f"upload staging directory {name!r} has no session")
                continue
            if name.startswith(".") and name.endswith(".tmp"):
                owner = name[1:].split(".", 1)[0]
                if owner not in active_upload_ids:
                    add("stale_upload_temp", "warning", detail=f"upload temporary file {name!r} has no active session")
                continue
            add("unexpected_upload_entry", "warning", detail=f"unexpected entry {name!r} in upload staging")

    objects_dir = root / "objects" / "sha256"
    total_objects = total_bytes = unreferenced = unreferenced_bytes = 0
    if objects_dir.is_dir():
        for shard in sorted(objects_dir.iterdir(), key=lambda item: item.name):
            if not shard.is_dir() or _SHARD.fullmatch(shard.name) is None:
                add("unexpected_object_entry", "warning", detail=f"unexpected object shard {shard.name!r}")
                continue
            for sub in sorted(shard.iterdir(), key=lambda item: item.name):
                if not sub.is_dir() or _SHARD.fullmatch(sub.name) is None:
                    add("unexpected_object_entry", "warning", detail=f"unexpected object shard {shard.name}/{sub.name}")
                    continue
                for candidate in sorted(sub.iterdir(), key=lambda item: item.name):
                    if not candidate.is_file():
                        add(
                            "unexpected_object_entry",
                            "warning",
                            detail=f"unexpected non-file object entry {shard.name}/{sub.name}/{candidate.name}",
                        )
                        continue
                    digest = candidate.name
                    if _HEX64.fullmatch(digest) is None or not digest.startswith(shard.name + sub.name):
                        add(
                            "unexpected_object_entry",
                            "warning",
                            detail=f"object {shard.name}/{sub.name}/{digest} is not at its digest path",
                        )
                        continue
                    try:
                        size = candidate.stat().st_size
                    except OSError:
                        add("unreadable_object_entry", "critical", detail=f"object {digest} could not be inspected")
                        orphan_check_complete = False
                        continue
                    total_objects += 1
                    total_bytes += size
                    if digest not in referenced:
                        unreferenced += 1
                        unreferenced_bytes += size
    object_store.update(
        count=total_objects,
        bytes=total_bytes,
        unreferenced=unreferenced if orphan_check_complete else None,
        unreferenced_bytes=unreferenced_bytes if orphan_check_complete else None,
        orphan_check_complete=orphan_check_complete,
    )
    return _report(issues, truncated, counts, object_store)
