"""Durable state for XeLibrary bulk row jobs (issue #83, plan §23).

Payloads are persisted in the catalogue database so a large batch never
depends on request memory: the API stores the batch, a background worker
applies it row by row, and progress/results stay readable across restarts.
Rows that were already applied before an interruption keep their per-item
results; unfinished jobs are marked failed on startup with the partial
results retained so callers can locate and resubmit the remainder.

Idempotency scope: a ``batch_id`` identifies one request from one caller for one
table and kind. The same scope with the same payload is a duplicate; the same
scope with a different payload is a conflict (never silently replayed).
"""

import hashlib
import json
import time
from pathlib import Path
from uuid import uuid4

from .common import connect, transaction

FINISHED_HISTORY = 200


def payload_digest(kind, table_id, payload):
    """Stable digest of the exact request payload for batch-id conflict checks."""
    canonical = json.dumps(
        {"kind": kind, "table": table_id, "rows": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


class BulkJobStore:
    def __init__(self, database):
        self.database = database
        self.stranded = []
        with transaction(database) as db:
            self._migrate(db)
            db.execute(
                "CREATE TABLE IF NOT EXISTS bulk_jobs("
                "id TEXT PRIMARY KEY,"
                "table_id TEXT NOT NULL,"
                "kind TEXT NOT NULL,"
                "batch_id TEXT,"
                "payload_digest TEXT NOT NULL,"
                "owner TEXT NOT NULL,"
                "payload TEXT NOT NULL,"
                "status TEXT NOT NULL,"
                "total INTEGER NOT NULL,"
                "done INTEGER NOT NULL DEFAULT 0,"
                "failed INTEGER NOT NULL DEFAULT 0,"
                "results TEXT NOT NULL DEFAULT '[]',"
                "error TEXT,"
                "output TEXT,"
                "cancel_requested INTEGER NOT NULL DEFAULT 0,"
                "created_at REAL NOT NULL,"
                "updated_at REAL NOT NULL)"
            )
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS bulk_jobs_batch_scope "
                "ON bulk_jobs(owner,table_id,kind,batch_id) WHERE batch_id IS NOT NULL"
            )
            db.execute("CREATE INDEX IF NOT EXISTS bulk_jobs_table_idx ON bulk_jobs(table_id)")
            stranded = db.execute(
                "SELECT * FROM bulk_jobs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in stranded:
                job = dict(row)
                job["payload"] = json.loads(job["payload"])
                job["results"] = json.loads(job["results"])
                self.stranded.append(job)
                self._write(
                    db,
                    row["id"],
                    status="failed",
                    error="worker interrupted; partial results retained; resubmit remaining items",
                )
            for row in db.execute("SELECT id,output FROM bulk_jobs WHERE status='cancelled'").fetchall():
                if row["output"]:
                    Path(row["output"]).unlink(missing_ok=True)
                    self._write(db, row["id"], output=None)
            self._prune(db)

    @staticmethod
    def _migrate(db):
        """Rebuild an older table whose batch_id was globally unique and had no digest/export columns."""
        columns = {row[1]: row for row in db.execute("PRAGMA table_info(bulk_jobs)")}
        indexes = {row[1]: row for row in db.execute("PRAGMA index_list(bulk_jobs)")}
        legacy_unique = any(
            row[1].startswith("sqlite_autoindex") and row[2] == 1 for row in indexes.values()
        )
        if not columns or ("payload_digest" in columns and not legacy_unique):
            return
        db.execute("ALTER TABLE bulk_jobs RENAME TO bulk_jobs_legacy")
        db.execute(
            "CREATE TABLE bulk_jobs("
            "id TEXT PRIMARY KEY,table_id TEXT NOT NULL,kind TEXT NOT NULL,batch_id TEXT,"
            "payload_digest TEXT NOT NULL,owner TEXT NOT NULL,payload TEXT NOT NULL,"
            "status TEXT NOT NULL,total INTEGER NOT NULL,done INTEGER NOT NULL DEFAULT 0,"
            "failed INTEGER NOT NULL DEFAULT 0,results TEXT NOT NULL DEFAULT '[]',error TEXT,"
            "output TEXT,cancel_requested INTEGER NOT NULL DEFAULT 0,"
            "created_at REAL NOT NULL,updated_at REAL NOT NULL)"
        )
        legacy_columns = {row[1] for row in db.execute("PRAGMA table_info(bulk_jobs_legacy)")}
        for row in db.execute("SELECT * FROM bulk_jobs_legacy").fetchall():
            values = dict(row)
            payload = json.loads(values["payload"])
            digest = payload_digest(values["kind"], values["table_id"], payload)
            db.execute(
                "INSERT INTO bulk_jobs(id,table_id,kind,batch_id,payload_digest,owner,payload,"
                "status,total,done,failed,results,error,output,cancel_requested,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    values["id"], values["table_id"], values["kind"], values["batch_id"], digest,
                    values["owner"], values["payload"], values["status"], values["total"],
                    values["done"], values["failed"], values["results"], values["error"],
                    values.get("output") if "output" in legacy_columns else None,
                    values.get("cancel_requested", 0) if "cancel_requested" in legacy_columns else 0,
                    values["created_at"], values["updated_at"],
                ),
            )
        db.execute("DROP TABLE bulk_jobs_legacy")

    def create(self, table_id, kind, payload, owner, batch_id=None, total=None):
        job_id = str(uuid4())
        stamp = time.time()
        digest = payload_digest(kind, table_id, payload)
        with transaction(self.database) as db:
            db.execute(
                "INSERT INTO bulk_jobs(id,table_id,kind,batch_id,payload_digest,owner,payload,status,"
                "total,done,failed,results,error,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,0,0,'[]',NULL,?,?)",
                (job_id, table_id, kind, batch_id, digest, owner, json.dumps(payload), "queued",
                 len(payload) if total is None else total, stamp, stamp),
            )
            self._prune(db)
        return job_id

    def get(self, job_id):
        with connect(self.database) as db:
            row = db.execute("SELECT * FROM bulk_jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["payload"] = json.loads(job["payload"])
        job["results"] = json.loads(job["results"])
        return job

    def find_batch(self, batch_id, owner=None, table_id=None, kind=None):
        """Look up an idempotent batch; optionally narrowed to its exact scope."""
        if not batch_id:
            return None
        query = "SELECT id FROM bulk_jobs WHERE batch_id=?"
        parameters: list = [batch_id]
        if owner is not None:
            query += " AND owner=?"
            parameters.append(owner)
        if table_id is not None:
            query += " AND table_id=?"
            parameters.append(table_id)
        if kind is not None:
            query += " AND kind=?"
            parameters.append(kind)
        query += " ORDER BY created_at DESC LIMIT 1"
        with connect(self.database) as db:
            row = db.execute(query, parameters).fetchone()
        return self.get(row["id"]) if row else None

    def active_for_table(self, table_id):
        with connect(self.database) as db:
            rows = db.execute(
                "SELECT id FROM bulk_jobs WHERE table_id=? AND status IN ('queued','running')",
                (table_id,),
            ).fetchall()
        return [row["id"] for row in rows]

    def start(self, job_id):
        stamp = time.time()
        with transaction(self.database) as db:
            db.execute(
                "UPDATE bulk_jobs SET status='running',updated_at=? WHERE id=? AND status='queued'",
                (stamp, job_id),
            )

    def record(self, job_id, result, failed):
        """Append one per-item result and advance the progress counters."""
        stamp = time.time()
        with transaction(self.database) as db:
            row = db.execute("SELECT done,failed,results FROM bulk_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                return
            results = json.loads(row["results"])
            results.append(result)
            done = row["done"] + 1
            failed_count = row["failed"] + (1 if failed else 0)
            status = "completed" if done >= (db.execute(
                "SELECT total FROM bulk_jobs WHERE id=?", (job_id,)
            ).fetchone()["total"]) else "running"
            db.execute(
                "UPDATE bulk_jobs SET done=?,failed=?,results=?,status=?,updated_at=? WHERE id=?",
                (done, failed_count, json.dumps(results), status, stamp, job_id),
            )

    def recover(self, marker_reader):
        """Reconcile results that were committed in a table but not recorded here.

        Each applied item writes its result in the same table transaction as the
        row mutation, so a crash between that commit and ``record()`` cannot lose
        the fact that the row was applied. ``marker_reader(table_id, job_id)``
        returns ``[(index, result_json, failed), ...]`` from the table database.
        """
        for job in self.stranded:
            try:
                markers = marker_reader(job["table_id"], job["id"])
            except Exception:  # noqa: BLE001 - a missing/broken table must not block startup
                continue
            current = self.get(job["id"])
            if current is None:
                continue
            recorded = len(current["results"])
            expected = recorded
            for index, result_json, failed in sorted(markers):
                if index < expected:
                    continue
                if index != expected:
                    break
                self.record(job["id"], json.loads(result_json), bool(failed))
                expected += 1
            if expected > recorded:
                self.fail(
                    job["id"],
                    "worker interrupted; partial results recovered from committed rows; "
                    "resubmit remaining items",
                )

    def fail(self, job_id, error):
        with transaction(self.database) as db:
            db.execute(
                "UPDATE bulk_jobs SET status='failed',error=?,updated_at=? WHERE id=?",
                (error, time.time(), job_id),
            )

    def complete(self, job_id):
        with transaction(self.database) as db:
            db.execute(
                "UPDATE bulk_jobs SET status='completed',updated_at=? WHERE id=? AND status='running'",
                (time.time(), job_id),
            )

    def set_done(self, job_id, done):
        with transaction(self.database) as db:
            db.execute(
                "UPDATE bulk_jobs SET done=?,status='running',updated_at=? WHERE id=? AND status IN ('queued','running')",
                (done, time.time(), job_id),
            )

    def request_cancel(self, job_id):
        """Flag a queued/running job for cooperative cancellation."""
        with transaction(self.database) as db:
            row = db.execute("SELECT status FROM bulk_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] in ("completed", "failed", "cancelled"):
                return False
            db.execute(
                "UPDATE bulk_jobs SET cancel_requested=1,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,"
                "updated_at=? WHERE id=?",
                (time.time(), job_id),
            )
            return True

    def cancel_requested(self, job_id):
        with connect(self.database) as db:
            row = db.execute("SELECT cancel_requested FROM bulk_jobs WHERE id=?", (job_id,)).fetchone()
        return bool(row and row["cancel_requested"])

    def finish_cancelled(self, job_id, output=None):
        with transaction(self.database) as db:
            row = db.execute("SELECT output FROM bulk_jobs WHERE id=?", (job_id,)).fetchone()
            if row and row["output"]:
                Path(row["output"]).unlink(missing_ok=True)
            db.execute(
                "UPDATE bulk_jobs SET status='cancelled',output=NULL,updated_at=? WHERE id=?",
                (time.time(), job_id),
            )

    def set_output(self, job_id, output):
        with transaction(self.database) as db:
            db.execute("UPDATE bulk_jobs SET output=?,updated_at=? WHERE id=?", (output, time.time(), job_id))

    def list(self, table_id=None, limit=50):
        query = "SELECT * FROM bulk_jobs"
        parameters: list = []
        if table_id:
            query += " WHERE table_id=?"
            parameters.append(table_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        with connect(self.database) as db:
            rows = [dict(row) for row in db.execute(query, parameters).fetchall()]
        for job in rows:
            job["results"] = json.loads(job["results"])
            job["payload"] = None  # payloads can be large; list view stays light
        return rows

    def _write(self, db, job_id, **changes):
        changes["updated_at"] = time.time()
        assignments = ",".join(f"{key}=?" for key in changes)
        db.execute(
            f"UPDATE bulk_jobs SET {assignments} WHERE id=?",
            (*changes.values(), job_id),
        )

    def _prune(self, db):
        db.execute(
            "DELETE FROM bulk_jobs WHERE status NOT IN ('queued','running') AND id NOT IN ("
            "SELECT id FROM bulk_jobs WHERE status NOT IN ('queued','running') "
            "ORDER BY created_at DESC LIMIT ?)",
            (FINISHED_HISTORY,),
        )
