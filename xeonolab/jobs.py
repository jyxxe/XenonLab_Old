"""Durable job state for the single-process indexing worker."""

import json
from datetime import datetime, timezone

from .common import connect, transaction


class JobStore:
    def __init__(self, database):
        self.database = database
        with transaction(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS index_jobs(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            for row in db.execute("SELECT id,payload FROM index_jobs").fetchall():
                data = json.loads(row["payload"])
                if data["status"] in ("queued", "running"):
                    data.update(status="failed", error="worker interrupted; submit a new reindex request")
                    self._write(db, row["id"], data)

    def _write(self, db, job_id, data):
        data["updated_at"] = datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO index_jobs VALUES(?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload",
            (job_id, json.dumps(data)),
        )

    def create(self, job_id, owner, table_id):
        with transaction(self.database) as db:
            self._write(
                db,
                job_id,
                {
                    "job_id": job_id,
                    "owner": owner,
                    "table_id": table_id,
                    "status": "queued",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
            )

    def update(self, job_id, **changes):
        with transaction(self.database) as db:
            row = db.execute("SELECT payload FROM index_jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise KeyError(job_id)
            data = json.loads(row[0])
            data.update(changes)
            self._write(db, job_id, data)

    def get(self, job_id):
        with connect(self.database) as db:
            row = db.execute("SELECT payload FROM index_jobs WHERE id=?", (job_id,)).fetchone()
        return json.loads(row[0]) if row else None
