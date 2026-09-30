import fcntl
import hashlib
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
from datetime import datetime, timezone
from pathlib import PurePosixPath
from uuid import UUID, uuid4

from fastapi import Depends, Header, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, etag, precondition, response, scope, transaction
from .query import evaluate, json_ast, parse, path_matches, sort_key
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


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


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


class ACLWrite(BaseModel):
    grants: dict[str, list[str]]


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
    with transaction(catalogue) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS tables(id TEXT PRIMARY KEY, path TEXT, name TEXT,
          owner TEXT, UNIQUE(path,name));
        CREATE TABLE IF NOT EXISTS acl(resource_type TEXT, resource_id TEXT, grants TEXT,
          PRIMARY KEY(resource_type,resource_id));
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY, actor TEXT, event TEXT, resource TEXT, at TEXT);
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

    def allowed(actor, table, action, row_id=None):
        if "xelibrary:admin" in actor.scopes:
            return True
        with connect(catalogue) as db:
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
            import sqlite3

            if isinstance(exc, sqlite3.IntegrityError):
                raise APIError(409, "TABLE_EXISTS", "table name already exists in path") from None
            raise
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
        visible = [t for t in tables if allowed(actor, t, "read")][: limit + 1]
        return response(
            request, visible[:limit], next_cursor=visible[limit - 1]["id"] if len(visible) > limit else None
        )

    @app.get("/api/v1/tables/{tid}")
    @app.get("/api/v1/tables/{tid}/schema")
    def get_table(tid: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        table = authorize(actor, tid, "read")
        with table_connect(tid) as db:
            data = {**table, **metadata(db), "columns": columns(db)}
        return JSONResponse(response(request, data), headers={"ETag": etag(data["table_revision"])})

    @app.delete("/api/v1/tables/{tid}")
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

            upload_database = root / "uploads.sqlite"
            if upload_database.exists():
                with connect(upload_database) as db:
                    active = db.execute(
                        "SELECT 1 FROM uploads WHERE table_id=? AND completed=0 AND cancelled=0 LIMIT 1", (tid,)
                    ).fetchone()
                if active:
                    raise APIError(409, "TABLE_UPLOAD_ACTIVE", "finish or cancel uploads before deleting the table")

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
            for raw in db.execute("SELECT * FROM data WHERE _deleted_at IS NULL"):
                row = decode(raw)
                db.execute(
                    "INSERT INTO __xl_fts VALUES(?,?)",
                    (row["_row_id"], " ".join(str(v) for v in row["values"].values())),
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
        if not allowed(actor, {**table, "path": path, "id": ""}, "write"):
            raise APIError(403, "XL_FORBIDDEN", "destination path permission denied")
        with table_transaction(tid) as db, transaction(catalogue) as catalog:
            meta = metadata(db)
            precondition(request, meta["table_revision"])
            try:
                catalog.execute("UPDATE tables SET path=? WHERE id=?", (path, tid))
            except Exception:
                raise APIError(409, "TABLE_EXISTS", "destination table already exists") from None
            db.execute(
                "UPDATE __xl_meta SET logical_path=?,table_revision=table_revision+1,updated_at=?",
                (path, now()),
            )
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
        return JSONResponse(response(request, row), status_code=201, headers={"ETag": etag(1)})

    @app.get("/api/v1/tables/{tid}/rows")
    def list_rows(
        tid: str,
        request: Request,
        limit: int = 100,
        cursor: str = "",
        include_deleted: bool = False,
        actor=Depends(scope("xelibrary:read")),
    ):
        table = authorize(actor, tid, "read")
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        with table_connect(tid) as db:
            rows = [
                decode(r)
                for r in db.execute(
                    "SELECT * FROM data WHERE (? OR _deleted_at IS NULL) AND _row_id>? ORDER BY _row_id",
                    (include_deleted, cursor),
                )
                if allowed(actor, table, "read", r["_row_id"])
            ][: limit + 1]
        return response(
            request, rows[:limit], next_cursor=rows[limit - 1]["_row_id"] if len(rows) > limit else None
        )

    @app.get("/api/v1/tables/{tid}/rows/{rid}")
    def get_row(tid: str, rid: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        authorize(actor, tid, "read", rid)
        with table_connect(tid) as db:
            row = load_row(db, rid)
        return JSONResponse(response(request, row), headers={"ETag": etag(row["_revision"])})

    @app.patch("/api/v1/tables/{tid}/rows/{rid}")
    def patch_row(
        tid: str, rid: str, body: RowWrite, request: Request, actor=Depends(scope("xelibrary:write"))
    ):
        authorize(actor, tid, "write", rid)
        with table_transaction(tid) as db:
            row = load_row(db, rid)
            precondition(request, row["_revision"])
            values = {**row["values"], **validate_values(columns(db), body.values, True)}
            db.execute(
                "UPDATE data SET values_json=?,_revision=_revision+1,_updated_at=? WHERE _row_id=?",
                (json.dumps(values), now(), rid),
            )
            row = load_row(db, rid)
            change(db, row, "update")
            db.execute("DELETE FROM __xl_fts WHERE row_id=?", (rid,))
            db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(str(x) for x in values.values())))
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
                for name, column in [
                    ("data", "_row_id"),
                    ("__xl_changes", "row_id"),
                    ("__xl_attachments", "row_id"),
                    ("__xl_idempotency", "row_id"),
                    ("__xl_fts", "row_id"),
                ]:
                    db.execute(f"DELETE FROM {name} WHERE {column}=?", (rid,))
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
            db.execute("UPDATE data SET _deleted_at=?,_revision=_revision+1 WHERE _row_id=?", (now(), rid))
            change(db, load_row(db, rid, True), "delete")
            db.execute("DELETE FROM __xl_fts WHERE row_id=?", (rid,))
        audit(actor, "row.deleted", f"{tid}:{rid}")
        return response(request, {"deleted": True})

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
        audit(actor, "row.restored", f"{tid}:{rid}")
        return JSONResponse(response(request, row), headers={"ETag": etag(row["_revision"])})

    def execute_query(body: QueryBody, request: Request, actor):
        plan = parse(body.expression)
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables ORDER BY id")]
        target = plan.offset + plan.limit
        matches = []
        for table in tables:
            if not path_matches(table["path"], plan.path) or not allowed(actor, table, "read"):
                continue
            with table_connect(table["id"]) as db:
                for raw in db.execute("SELECT * FROM data WHERE _deleted_at IS NULL ORDER BY _row_id"):
                    row = decode(raw)
                    if allowed(actor, table, "read", row["_row_id"]) and evaluate(
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

    install(app, catalogue, file, authorize, allowed, load_row, table_transaction, table_connect)
    install_theorems(app, catalogue, allowed, audit)

    @app.post("/api/v1/search/hybrid")
    def hybrid(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
        raise APIError(503, "HYBRID_NOT_CONFIGURED", "hybrid fusion not yet implemented")

    @app.post("/api/v1/search/text")
    def text_search(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
        # Literal term query: user input cannot inject FTS operators.
        terms = body.query.split()
        fts_query = " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)
        if not fts_query:
            raise APIError(422, "EMPTY_QUERY", "query is empty")
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables ORDER BY id")]
        results = []
        for table in tables:
            if not path_matches(table["path"], body.path) or not allowed(actor, table, "read"):
                continue
            with table_connect(table["id"]) as db:
                for hit in db.execute(
                    "SELECT row_id,bm25(__xl_fts) AS rank FROM __xl_fts WHERE __xl_fts MATCH ?", (fts_query,)
                ):
                    if not allowed(actor, table, "read", hit["row_id"]):
                        continue
                    try:
                        row = load_row(db, hit["row_id"])
                    except APIError:
                        continue
                    if any(row["values"].get(k) != v for k, v in body.filters.items()):
                        continue
                    results.append(
                        {
                            "table_id": table["id"],
                            "row_id": hit["row_id"],
                            "row_revision": row["_revision"],
                            "path": table["path"],
                            "score": -hit["rank"],
                            "score_type": "sqlite_fts_bm25",
                            "snippet": " ".join(str(v) for v in row["values"].values())[:240],
                            "approximate": False,
                        }
                    )
        results.sort(key=lambda x: x["score"], reverse=True)
        return response(request, results[: body.limit], partial=False)

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
