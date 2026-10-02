"""Durable request history behind the visible embedding queue (issue #74).

Every request that reaches the D100M worker is recorded with its outcome, so
the desktop queue view can show unfinished requests plus the most recent
finished ones. Finished rows are pruned to a rolling window; unfinished rows
are always kept and marked failed on startup, mirroring index_jobs recovery.
"""

import time

from .common import connect, transaction

FINISHED_HISTORY = 1000
RUNNING = "running"
COMPLETED = "completed"
FAILED = "failed"


class EmbeddingRequestStore:
    def __init__(self, database):
        self.database = database
        with transaction(database) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS embedding_requests("
                "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "created_at REAL NOT NULL,"
                "finished_at REAL,"
                "status TEXT NOT NULL,"
                "model TEXT NOT NULL,"
                "batch_size INTEGER NOT NULL,"
                "input_chars INTEGER NOT NULL,"
                "truncation TEXT NOT NULL,"
                "tokens INTEGER,"
                "duration_ms INTEGER,"
                "error_code TEXT,"
                "caller TEXT)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS embedding_requests_status_idx "
                "ON embedding_requests(status)"
            )
            stranded = db.execute(
                "SELECT id FROM embedding_requests WHERE status=?", (RUNNING,)
            ).fetchall()
            for row in stranded:
                self._finish(db, row["id"], FAILED, error_code="WORKER_RESTARTED")
            self._prune(db)

    def start(self, model, batch_size, input_chars, truncation, caller):
        """Record a request as running and return its queue id."""
        with transaction(self.database) as db:
            cursor = db.execute(
                "INSERT INTO embedding_requests("
                "created_at,status,model,batch_size,input_chars,truncation,caller) "
                "VALUES(?,?,?,?,?,?,?)",
                (time.time(), RUNNING, model, batch_size, input_chars, truncation, caller),
            )
            return cursor.lastrowid

    def finish(self, request_id, status, tokens=None, error_code=None):
        with transaction(self.database) as db:
            self._finish(db, request_id, status, tokens=tokens, error_code=error_code)
            self._prune(db)

    def _finish(self, db, request_id, status, tokens=None, error_code=None):
        finished = time.time()
        db.execute(
            "UPDATE embedding_requests SET status=?,finished_at=?,tokens=?,"
            "duration_ms=CAST((?-created_at)*1000 AS INTEGER),error_code=? "
            "WHERE id=? AND status=?",
            (status, finished, tokens, finished, error_code, request_id, RUNNING),
        )

    def _prune(self, db):
        """Keep only the newest finished rows; running rows are never pruned."""
        db.execute(
            "DELETE FROM embedding_requests WHERE status!=? AND id NOT IN ("
            "SELECT id FROM embedding_requests WHERE status!=? ORDER BY id DESC LIMIT ?)",
            (RUNNING, RUNNING, FINISHED_HISTORY),
        )

    def active(self, caller=None):
        """Running requests. The queue view is caller-scoped (issue #74 review):
        history, activity and counts must never expose another principal's
        subject IDs, request metadata or errors."""
        query = "SELECT * FROM embedding_requests WHERE status=?"
        parameters: list = [RUNNING]
        if caller is not None:
            query += " AND caller=?"
            parameters.append(caller)
        query += " ORDER BY id"
        with connect(self.database) as db:
            rows = db.execute(query, parameters).fetchall()
        return [dict(row) for row in rows]

    def history(self, limit=100, cursor="", status=None, caller=None):
        """Newest-first finished and running requests; cursor is the last seen id."""
        query = "SELECT * FROM embedding_requests"
        parameters: list = []
        conditions = []
        if cursor:
            conditions.append("id<?")
            parameters.append(int(cursor))
        if status:
            conditions.append("status=?")
            parameters.append(status)
        if caller is not None:
            conditions.append("caller=?")
            parameters.append(caller)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id DESC LIMIT ?"
        parameters.append(limit + 1)
        with connect(self.database) as db:
            rows = [dict(row) for row in db.execute(query, parameters).fetchall()]
        next_cursor = str(rows[limit - 1]["id"]) if len(rows) > limit else None
        return rows[:limit], next_cursor

    def counts(self, caller=None):
        query = "SELECT status,COUNT(*) AS total FROM embedding_requests"
        parameters: list = []
        if caller is not None:
            query += " WHERE caller=?"
            parameters.append(caller)
        query += " GROUP BY status"
        with connect(self.database) as db:
            rows = db.execute(query, parameters).fetchall()
        observed = {row["status"]: row["total"] for row in rows}
        return {status: observed.get(status, 0) for status in (RUNNING, COMPLETED, FAILED)}
