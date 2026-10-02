"""Durable state for XeLibrary bulk row jobs (issue #83, plan §23).

Payloads are persisted in the catalogue database so a large batch never
depends on request memory: the API stores the batch, a background worker
applies it row by row, and progress/results stay readable across restarts.
Rows that were already applied before an interruption keep their per-item
results; unfinished jobs are marked failed on startup with the partial
results retained so callers can locate and resubmit the remainder.
"""

import json
import time
from uuid import uuid4

from .common import connect, transaction

FINISHED_HISTORY = 200


class BulkJobStore:
    def __init__(self, database):
        self.database = database
        with transaction(database) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS bulk_jobs("
                "id TEXT PRIMARY KEY,"
                "table_id TEXT NOT NULL,"
                "kind TEXT NOT NULL,"
                "batch_id TEXT UNIQUE,"
                "owner TEXT NOT NULL,"
                "payload TEXT NOT NULL,"
                "status TEXT NOT NULL,"
                "total INTEGER NOT NULL,"
                "done INTEGER NOT NULL DEFAULT 0,"
                "failed INTEGER NOT NULL DEFAULT 0,"
                "results TEXT NOT NULL DEFAULT '[]',"
                "error TEXT,"
                "created_at REAL NOT NULL,"
                "updated_at REAL NOT NULL)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS bulk_jobs_table_idx ON bulk_jobs(table_id)")
            stranded = db.execute(
                "SELECT id FROM bulk_jobs WHERE status IN ('queued','running')"
            ).fetchall()
            for row in stranded:
                self._write(
                    db,
                    row["id"],
                    status="failed",
                    error="worker interrupted; partial results retained; resubmit remaining items",
                )
            self._prune(db)

    def create(self, table_id, kind, payload, owner, batch_id=None):
        job_id = str(uuid4())
        stamp = time.time()
        with transaction(self.database) as db:
            db.execute(
                "INSERT INTO bulk_jobs(id,table_id,kind,batch_id,owner,payload,status,total,"
                "done,failed,results,error,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,0,0,'[]',NULL,?,?)",
                (job_id, table_id, kind, batch_id, owner, json.dumps(payload), "queued",
                 len(payload), stamp, stamp),
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

    def find_batch(self, batch_id):
        if not batch_id:
            return None
        with connect(self.database) as db:
            row = db.execute("SELECT id FROM bulk_jobs WHERE batch_id=?", (batch_id,)).fetchone()
        return self.get(row["id"]) if row else None

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

    def fail(self, job_id, error):
        with transaction(self.database) as db:
            db.execute(
                "UPDATE bulk_jobs SET status='failed',error=?,updated_at=? WHERE id=?",
                (error, time.time(), job_id),
            )

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
