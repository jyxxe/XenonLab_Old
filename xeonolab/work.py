"""Durable, fenced collaboration claims. Issue text never becomes executable input."""

import time
from uuid import uuid4

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, connect, response, scope, transaction


class WorkCreate(BaseModel):
    issue_id: int = Field(ge=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=200)


class Claim(BaseModel):
    ttl_seconds: int = Field(default=900, ge=30, le=3600)


class Release(BaseModel):
    epoch: int = Field(ge=1)
    completed: bool = False


def install(app):
    database = app.state.settings.data_dir / "work.sqlite"
    with transaction(database) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work(id TEXT PRIMARY KEY,repository TEXT,issue_id INTEGER,
          title TEXT,status TEXT,holder TEXT,expires REAL,epoch INTEGER,
          UNIQUE(repository,issue_id));
        CREATE TABLE IF NOT EXISTS work_audit(id TEXT PRIMARY KEY,actor TEXT,event TEXT,
          work_id TEXT,epoch INTEGER,at REAL);
        """)

    def load(db, work_id):
        row = db.execute("SELECT * FROM work WHERE id=?", (work_id,)).fetchone()
        if not row:
            raise APIError(404, "WORK_NOT_FOUND", "work item not found")
        return dict(row)

    def audit(db, actor, event, work_id, epoch):
        db.execute(
            "INSERT INTO work_audit VALUES(?,?,?,?,?,?)",
            (str(uuid4()), actor.sub, event, work_id, epoch, time.time()),
        )

    @app.post("/api/v1/work", status_code=201)
    def create(body: WorkCreate, request: Request, actor=Depends(scope("dev:write"))):
        with transaction(database) as db:
            old = db.execute(
                "SELECT * FROM work WHERE repository=? AND issue_id=?", (body.repository, body.issue_id)
            ).fetchone()
            if old:
                return response(request, dict(old))
            work_id = str(uuid4())
            db.execute(
                "INSERT INTO work VALUES(?,?,?,?,?,?,?,?)",
                (work_id, body.repository, body.issue_id, body.title, "queued", None, 0, 0),
            )
            audit(db, actor, "work.created", work_id, 0)
            return response(request, load(db, work_id))

    @app.get("/api/v1/work")
    def listing(request: Request, limit: int = 100, cursor: str = "", actor=Depends(scope("dev:read"))):
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        with connect(database) as db:
            rows = [
                dict(r)
                for r in db.execute("SELECT * FROM work WHERE id>? ORDER BY id LIMIT ?", (cursor, limit + 1))
            ]
        for row in rows:
            row["claim_active"] = row["holder"] is not None and row["expires"] > time.time()
        return response(
            request, rows[:limit], next_cursor=rows[limit - 1]["id"] if len(rows) > limit else None
        )

    @app.post("/api/v1/work/{work_id}/claim")
    def claim(work_id: str, body: Claim, request: Request, actor=Depends(scope("dev:write"))):
        with transaction(database) as db:
            item = load(db, work_id)
            if item["status"] == "completed":
                raise APIError(409, "WORK_COMPLETED", "completed task cannot be claimed")
            if item["holder"] is not None and item["expires"] > time.time():
                raise APIError(409, "WORK_CLAIMED", "task has an active claim")
            epoch = item["epoch"] + 1
            db.execute(
                "UPDATE work SET holder=?,expires=?,epoch=?,status='claimed' WHERE id=?",
                (actor.sub, time.time() + body.ttl_seconds, epoch, work_id),
            )
            audit(db, actor, "work.claimed", work_id, epoch)
            return response(request, load(db, work_id))

    @app.post("/api/v1/work/{work_id}/release")
    def release(work_id: str, body: Release, request: Request, actor=Depends(scope("dev:write"))):
        with transaction(database) as db:
            item = load(db, work_id)
            if item["holder"] != actor.sub or item["epoch"] != body.epoch or item["expires"] <= time.time():
                raise APIError(409, "STALE_CLAIM", "active holder and current epoch required")
            db.execute(
                "UPDATE work SET holder=NULL,expires=0,status=? WHERE id=?",
                ("completed" if body.completed else "queued", work_id),
            )
            audit(db, actor, "work.completed" if body.completed else "work.released", work_id, body.epoch)
            return response(request, load(db, work_id))
