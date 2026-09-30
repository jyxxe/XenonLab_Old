"""Durable, fenced collaboration claims. Issue text never becomes executable input."""

import hashlib
import json
import os
import ssl
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, connect, response, scope, transaction


class WorkCreate(BaseModel):
    issue_id: int = Field(ge=1)
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=200)


class IssueLink(BaseModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    target_issue_id: int = Field(ge=1)
    type: str = Field(pattern=r"^(duplicate_of|related_to|blocked_by|blocks|supersedes)$")


class Reconcile(BaseModel):
    repository: str = Field(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


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
        CREATE TABLE IF NOT EXISTS issue_sync(repository TEXT,issue_id INTEGER,updated REAL,
          PRIMARY KEY(repository,issue_id));
        CREATE TABLE IF NOT EXISTS issue_snapshots(repository TEXT,issue_id INTEGER,digest TEXT,
          conflict INTEGER DEFAULT 0,PRIMARY KEY(repository,issue_id));
        CREATE TABLE IF NOT EXISTS issue_links(repository TEXT,source INTEGER,target INTEGER,kind TEXT,
          actor TEXT,created REAL,PRIMARY KEY(repository,source,target,kind));
        CREATE TABLE IF NOT EXISTS work_audit(id TEXT PRIMARY KEY,actor TEXT,event TEXT,
          work_id TEXT,epoch INTEGER,at REAL);
        """)

    def repository_allowed(repository):
        allowed = os.getenv("DEV_REPOSITORIES", "xenon-admin/XeonoLab").split(",")
        if app.state.settings.mode == "production" and repository not in allowed:
            raise APIError(403, "REPOSITORY_FORBIDDEN", "repository outside service scope")

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

    def ingest(db, payload, authoritative=False):
        if not isinstance(payload, dict) or not isinstance(payload.get("issue"), dict):
            return
        metadata = payload.get("repository")
        if not isinstance(metadata, dict):
            raise APIError(422, "INVALID_ISSUE", "repository metadata required")
        repository = metadata.get("full_name", "")
        if repository not in os.getenv("DEV_REPOSITORIES", "xenon-admin/XeonoLab").split(","):
            raise APIError(403, "REPOSITORY_FORBIDDEN", "webhook repository outside service scope")
        issue = payload["issue"]
        number, title, state = issue.get("number"), issue.get("title"), issue.get("state")
        if type(number) is not int or number < 1 or not isinstance(title, str) or not 1 <= len(title) <= 200:
            raise APIError(422, "INVALID_ISSUE", "invalid issue metadata")
        if state not in ("open", "closed"):
            raise APIError(422, "INVALID_ISSUE", "invalid issue state")
        try:
            updated = datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
            if updated.tzinfo is None:
                raise ValueError()
            stamp = updated.timestamp()
        except (KeyError, ValueError, TypeError, AttributeError):
            raise APIError(422, "INVALID_ISSUE", "timezone-aware updated_at required") from None
        previous = db.execute(
            "SELECT updated FROM issue_sync WHERE repository=? AND issue_id=?", (repository, number)
        ).fetchone()
        digest = hashlib.sha256(
            json.dumps({"title": title, "state": state}, sort_keys=True).encode()
        ).hexdigest()
        snapshot = db.execute(
            "SELECT * FROM issue_snapshots WHERE repository=? AND issue_id=?", (repository, number)
        ).fetchone()
        if previous and previous[0] > stamp:
            if authoritative:
                raise APIError(409, "SOURCE_STALE", "source older than recorded event")
            return
        if previous and previous[0] == stamp and not authoritative:
            if snapshot and snapshot["digest"] == digest and not snapshot["conflict"]:
                return
            db.execute(
                "UPDATE work SET status='blocked',holder=NULL,expires=0,epoch=epoch+1 "
                "WHERE repository=? AND issue_id=?",
                (repository, number),
            )
            db.execute(
                "INSERT INTO issue_snapshots VALUES(?,?,?,1) ON CONFLICT(repository,issue_id) "
                "DO UPDATE SET conflict=1",
                (repository, number, digest),
            )
            item = db.execute(
                "SELECT id,epoch FROM work WHERE repository=? AND issue_id=?", (repository, number)
            ).fetchone()
            db.execute(
                "INSERT INTO work_audit VALUES(?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    "forgejo-webhook",
                    "issue.sync_conflict",
                    item["id"],
                    item["epoch"],
                    time.time(),
                ),
            )
            return
        item = db.execute(
            "SELECT * FROM work WHERE repository=? AND issue_id=?", (repository, number)
        ).fetchone()
        if item is None:
            work_id = str(uuid4())
            db.execute(
                "INSERT INTO work VALUES(?,?,?,?,?,?,?,?)",
                (
                    work_id,
                    repository,
                    number,
                    title,
                    "completed" if state == "closed" else "queued",
                    None,
                    0,
                    0,
                ),
            )
        else:
            work_id = item["id"]
            db.execute("UPDATE work SET title=? WHERE id=?", (title, work_id))
            if state == "closed" or (state == "open" and item["status"] in ("completed", "blocked")):
                db.execute(
                    "UPDATE work SET status=?,holder=NULL,expires=0,epoch=epoch+1 WHERE id=?",
                    ("completed" if state == "closed" else "queued", work_id),
                )
        db.execute(
            "INSERT INTO issue_snapshots VALUES(?,?,?,0) ON CONFLICT(repository,issue_id) "
            "DO UPDATE SET digest=excluded.digest,conflict=0",
            (repository, number, digest),
        )
        db.execute(
            "INSERT INTO issue_sync VALUES(?,?,?) ON CONFLICT(repository,issue_id) "
            "DO UPDATE SET updated=excluded.updated",
            (repository, number, stamp),
        )
        db.execute(
            "INSERT INTO work_audit VALUES(?,?,?,?,?,?)",
            (
                str(uuid4()),
                "forgejo-webhook",
                "issue.synced:" + state,
                work_id,
                db.execute("SELECT epoch FROM work WHERE id=?", (work_id,)).fetchone()[0],
                time.time(),
            ),
        )

    app.state.ingest_forgejo = ingest

    @app.post("/api/v1/issues/{issue_id}/reconcile")
    def reconcile(issue_id: int, body: Reconcile, request: Request, actor=Depends(scope("dev:write"))):
        repository_allowed(body.repository)
        if issue_id < 1:
            raise APIError(422, "INVALID_ISSUE", "positive issue number required")
        endpoint = os.getenv("FORGEJO_URL")
        if not endpoint:
            raise APIError(503, "FORGEJO_NOT_CONFIGURED", "source API not configured")
        context = ssl.create_default_context()
        if os.getenv("FORGEJO_CA_FILE"):
            context.load_verify_locations(Path(os.environ["FORGEJO_CA_FILE"]))
        try:
            with httpx.Client(verify=context, trust_env=False, timeout=30) as client:
                result = client.get(
                    endpoint.rstrip("/") + "/api/v1/repos/" + body.repository + "/issues/" + str(issue_id)
                )
                result.raise_for_status()
                issue = result.json()
        except (httpx.HTTPError, ValueError):
            raise APIError(503, "FORGEJO_UNAVAILABLE", "source state could not be verified") from None
        if not isinstance(issue, dict) or issue.get("number") != issue_id:
            raise APIError(503, "INVALID_SOURCE", "source returned a different issue")
        with transaction(database) as db:
            ingest(db, {"repository": {"full_name": body.repository}, "issue": issue}, authoritative=True)
            item = db.execute(
                "SELECT * FROM work WHERE repository=? AND issue_id=?", (body.repository, issue_id)
            ).fetchone()
            audit(db, actor, "issue.reconciled", item["id"], item["epoch"])
            return response(request, dict(item))

    @app.post("/api/v1/work", status_code=201)
    def create(body: WorkCreate, request: Request, actor=Depends(scope("dev:write"))):
        repository_allowed(body.repository)
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
            if item["status"] == "blocked":
                raise APIError(409, "SYNC_CONFLICT", "resolve source conflict before claiming")
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

    @app.post("/api/v1/issues/{issue_id}/links", status_code=201)
    def link(issue_id: int, body: IssueLink, request: Request, actor=Depends(scope("dev:write"))):
        repository_allowed(body.repository)
        if issue_id == body.target_issue_id:
            raise APIError(422, "SELF_LINK", "cannot link an issue to itself")
        with transaction(database) as db:
            for number in (issue_id, body.target_issue_id):
                if not db.execute(
                    "SELECT 1 FROM work WHERE repository=? AND issue_id=?", (body.repository, number)
                ).fetchone():
                    raise APIError(404, "ISSUE_NOT_REGISTERED", "register both issues before linking")
            if body.type == "duplicate_of":
                cycle = db.execute(
                    """WITH RECURSIVE targets(id) AS (
                    SELECT ? UNION SELECT l.target FROM issue_links l JOIN targets t ON l.source=t.id
                    WHERE l.repository=? AND l.kind='duplicate_of')
                    SELECT 1 FROM targets WHERE id=?""",
                    (body.target_issue_id, body.repository, issue_id),
                ).fetchone()
                if cycle:
                    raise APIError(409, "DUPLICATE_CYCLE", "duplicate relation would create a cycle")
            db.execute(
                "INSERT OR IGNORE INTO issue_links VALUES(?,?,?,?,?,?)",
                (body.repository, issue_id, body.target_issue_id, body.type, actor.sub, time.time()),
            )
            item = db.execute(
                "SELECT id,epoch FROM work WHERE repository=? AND issue_id=?", (body.repository, issue_id)
            ).fetchone()
            audit(db, actor, "issue.linked:" + body.type, item["id"], item["epoch"])
        return response(
            request,
            {
                "repository": body.repository,
                "issue_id": issue_id,
                "target_issue_id": body.target_issue_id,
                "type": body.type,
            },
        )

    @app.get("/api/v1/issues/{issue_id}/links")
    def links(issue_id: int, repository: str, request: Request, actor=Depends(scope("dev:read"))):
        repository_allowed(repository)
        with connect(database) as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM issue_links WHERE repository=? AND (source=? OR target=?) ORDER BY created",
                    (repository, issue_id, issue_id),
                )
            ]
        return response(request, rows)
