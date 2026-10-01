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


class Renew(BaseModel):
    epoch: int = Field(ge=1)
    ttl_seconds: int = Field(default=900, ge=30, le=3600)


def repository_allowed(settings, repository):
    allowed = os.getenv("DEV_REPOSITORIES", "xenon-admin/XeonoLab").split(",")
    if settings.mode == "production" and repository not in allowed:
        raise APIError(403, "REPOSITORY_FORBIDDEN", "repository outside service scope")


class ForgejoClaimSync:
    """Track this request's writes without retaining credentials in public details."""

    def __init__(self, item):
        self.work_id, self.epoch = item["id"], item["epoch"]
        self.assignee, self.comment = "not_attempted", "not_attempted"
        self.comment_id = None
        self.client = None
        self.root, self.headers = "", {}

    def details(self):
        return {
            "work_id": self.work_id,
            "claim_epoch": self.epoch,
            "forgejo_sync": {
                "assignee": self.assignee,
                "comment": self.comment,
                "comment_id": self.comment_id,
                "reconciliation_required": (
                    self.assignee in ("added", "unknown")
                    or self.comment in ("created", "unknown", "cleanup_failed")
                ),
            },
        }

    def discard_comment(self):
        # Delete only the ID returned for this request. Never search by body/epoch:
        # a timed-out POST may have succeeded, but its comment cannot be identified.
        if self.comment_id is not None:
            try:
                url = self.root.rsplit("/", 1)[0] + "/comments/" + str(self.comment_id)
                result = self.client.delete(url, headers=self.headers)
                result.raise_for_status()
                self.comment = "deleted"
            except Exception:
                self.comment = "cleanup_failed"
        # Assignees are shared, unversioned state in Forgejo. Replacing an old set
        # could remove a new holder or another user's assignment, even after a GET.

    def close(self):
        if self.client is not None:
            try:
                self.client.close()
            except Exception:
                pass


def sync_forgejo_claim(item, actor, settings):
    """Mirror a durable Work claim to Forgejo when the write integration is configured.

    Forgejo credentials and subject-to-account mapping are deployment-owned files. In
    production the integration is mandatory: silently claiming without assigning the
    Issue would make the collaboration contract appear to have succeeded when it had
    not. Development tests may exercise the local queue without Forgejo configured.
    """
    token_file = os.getenv("FORGEJO_CLAIM_TOKEN_FILE")
    users_file = os.getenv("FORGEJO_AGENT_USERS_FILE")
    endpoint = os.getenv("FORGEJO_URL")
    if not token_file or not users_file or not endpoint:
        if settings.mode == "production":
            raise APIError(503, "FORGEJO_CLAIM_NOT_CONFIGURED", "Issue claim synchronization is unavailable")
        return

    try:
        token = Path(token_file).read_text().strip()
        users = json.loads(Path(users_file).read_text())
        username = users.get(actor.sub) if isinstance(users, dict) else None
    except (OSError, ValueError):
        raise APIError(503, "FORGEJO_CLAIM_NOT_CONFIGURED", "Issue claim synchronization is unavailable") from None
    if not token or not isinstance(username, str) or not username:
        raise APIError(503, "FORGEJO_CLAIM_NOT_CONFIGURED", "Issue claim synchronization is unavailable")

    root = endpoint.rstrip("/") + "/api/v1/repos/" + item["repository"] + "/issues/" + str(item["issue_id"])
    headers = {"Authorization": "token " + token}
    sync = ForgejoClaimSync(item)
    sync.root, sync.headers = root, headers
    succeeded = False
    try:
        context = ssl.create_default_context()
        if os.getenv("FORGEJO_CA_FILE"):
            context.load_verify_locations(Path(os.environ["FORGEJO_CA_FILE"]))
        client = sync.client = httpx.Client(verify=context, trust_env=False, timeout=10)
        issue_response = client.get(root, headers=headers)
        issue_response.raise_for_status()
        issue = issue_response.json()
        if issue.get("number") != item["issue_id"] or issue.get("state") != "open":
            raise APIError(409, "ISSUE_NOT_OPEN", "Forgejo Issue is no longer open")
        # Forgejo returns null as well as [] for an unassigned Issue.
        assignees = issue.get("assignees")
        if assignees is None:
            assignees = []
        old_assignees = [entry["login"] for entry in assignees if isinstance(entry, dict) and entry.get("login")]
        if username not in old_assignees:
            sync.assignee = "unknown"
            assigned = client.patch(root, headers=headers, json={"assignees": old_assignees + [username]})
            assigned.raise_for_status()
            sync.assignee = "added"
        else:
            sync.assignee = "unchanged"
        comment_url = root + "/comments"
        sync.comment = "unknown"
        comment = client.post(
            comment_url,
            headers=headers,
            json={"body": (
                f"Harness claim: @{username} claimed this Issue (Work epoch {item['epoch']}). "
                "Verify the active holder and epoch via the Work API before working."
            )},
        )
        comment.raise_for_status()
        comment_id = comment.json().get("id")
        if type(comment_id) is not int or comment_id < 1:
            raise ValueError("missing comment ID")
        sync.comment_id, sync.comment = comment_id, "created"
        succeeded = True
        return sync
    except APIError:
        raise
    except Exception:
        # A timeout may follow a committed remote write. Do not claim rollback or
        # overwrite the shared assignee set; expose sanitized uncertainty instead.
        raise APIError(
            503, "FORGEJO_CLAIM_SYNC_FAILED",
            "Issue claim could not be synchronized; remote side effects may remain (see details)",
            details=sync.details(),
        ) from None
    finally:
        if not succeeded:
            sync.close()


def install(app):
    database = app.state.settings.data_dir / "work.sqlite"
    with transaction(database) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS work(id TEXT PRIMARY KEY,repository TEXT,issue_id INTEGER,
          title TEXT,status TEXT,holder TEXT,expires REAL,epoch INTEGER,created_at REAL NOT NULL DEFAULT 0,
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
        if "created_at" not in {row[1] for row in db.execute("PRAGMA table_info(work)")}:
            # Issue #92: existing rows keep 0, which sorts them first (oldest) in FIFO order.
            db.execute("ALTER TABLE work ADD COLUMN created_at REAL NOT NULL DEFAULT 0")

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
                "INSERT INTO work VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    work_id,
                    repository,
                    number,
                    title,
                    "completed" if state == "closed" else "queued",
                    None,
                    0,
                    0,
                    time.time(),
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
        repository_allowed(app.state.settings, body.repository)
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
        repository_allowed(app.state.settings, body.repository)
        with transaction(database) as db:
            old = db.execute(
                "SELECT * FROM work WHERE repository=? AND issue_id=?", (body.repository, body.issue_id)
            ).fetchone()
            if old:
                return response(request, dict(old))
            work_id = str(uuid4())
            db.execute(
                "INSERT INTO work VALUES(?,?,?,?,?,?,?,?,?)",
                (work_id, body.repository, body.issue_id, body.title, "queued", None, 0, 0, time.time()),
            )
            audit(db, actor, "work.created", work_id, 0)
            return response(request, load(db, work_id))

    @app.get("/api/v1/work")
    def listing(
        request: Request,
        limit: int = 100,
        cursor: str = "",
        active: str = "",
        status: str = "",
        repository: str = "",
        issue_id: int | None = None,
        actor=Depends(scope("dev:read")),
    ):
        """Lease semantics and queue ordering (issues #92, #93).

        Rows are ordered FIFO by creation time (oldest first) with the id as a
        deterministic tiebreak, so pagination is stable while new items arrive.
        The cursor is `<created_at>:<id>`. `active=true|false` and the optional
        `status` (comma-separated), `repository` and `issue_id` filters apply in
        SQL; `meta.counts` always reports status totals for the whole queue.
        """
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        if active not in ("", "true", "false"):
            raise APIError(422, "INVALID_ACTIVE", "active must be true or false")
        valid_statuses = ("queued", "claimed", "completed", "blocked")
        statuses = [item for item in status.split(",") if item]
        if any(item not in valid_statuses for item in statuses):
            raise APIError(422, "INVALID_STATUS", "unknown status filter")
        now = time.time()
        active_expression = "(status='queued' OR (holder IS NOT NULL AND expires > :now))"
        conditions, parameters = [], {"now": now}
        if cursor:
            try:
                cursor_created_raw, cursor_id = cursor.split(":", 1)
                cursor_created = float(cursor_created_raw)
            except ValueError:
                raise APIError(422, "INVALID_CURSOR", "cursor must be '<created_at>:<id>'") from None
            conditions.append("(created_at > :cursor_created OR (created_at = :cursor_created AND id > :cursor_id))")
            parameters["cursor_created"] = cursor_created
            parameters["cursor_id"] = cursor_id
        if active:
            conditions.append(active_expression if active == "true" else f"NOT {active_expression}")
        if statuses:
            conditions.append("status IN (" + ",".join(f":status{i}" for i in range(len(statuses))) + ")")
            parameters.update({f"status{i}": item for i, item in enumerate(statuses)})
        if repository:
            conditions.append("repository = :repository")
            parameters["repository"] = repository
        if issue_id is not None:
            conditions.append("issue_id = :issue_id")
            parameters["issue_id"] = issue_id
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        query = (
            "SELECT *,"
            f" {active_expression} AS claim_active,"
            f" (status='claimed' AND NOT {active_expression}) AS lease_expired"
            f" FROM work{where} ORDER BY created_at ASC, id ASC LIMIT :limit"
        )
        parameters["limit"] = limit + 1
        with connect(database) as db:
            rows = [dict(r) for r in db.execute(query, parameters)]
            counts = {name: 0 for name in valid_statuses}
            for counted in db.execute("SELECT status, COUNT(*) AS total FROM work GROUP BY status"):
                counts[counted["status"]] = counted["total"]
        for row in rows:
            # SQLite yields the computed columns as 0/1; the API contract is JSON booleans.
            row["claim_active"] = bool(row["claim_active"])
            row["lease_expired"] = bool(row["lease_expired"])
        next_cursor = f"{rows[limit - 1]['created_at']}:{rows[limit - 1]['id']}" if len(rows) > limit else None
        return response(request, rows[:limit], next_cursor=next_cursor, counts=counts)

    @app.post(
        "/api/v1/work/{work_id}/claim",
        responses={
            409: {
                "description": (
                    "Returns the standard `error` envelope (`code`, `message`, `details`, `request_id`). "
                    "Codes: `WORK_CLAIMED`, `WORK_COMPLETED`, `SYNC_CONFLICT`, `ISSUE_NOT_OPEN`, or "
                    "`STALE_CLAIM` when the claim was fenced during Forgejo synchronization. In that case "
                    "only the comment ID created by this request is deleted, best effort; assignees are "
                    "left untouched. `details.forgejo_sync` reports remote write/cleanup outcomes and "
                    "`reconciliation_required`; a stale assignee or comment may remain."
                )
            },
            503: {
                "description": (
                    "Returns the standard `error` envelope (`code`, `message`, `details`, `request_id`); "
                    "codes are `FORGEJO_CLAIM_NOT_CONFIGURED` or `FORGEJO_CLAIM_SYNC_FAILED`. The request's "
                    "holder+epoch claim is conditionally reset only while that exact claim remains current. "
                    "If a concurrent webhook or reclaim changed the lease, its newer state is preserved. "
                    "Assignees are never restored from an old snapshot. `FORGEJO_CLAIM_SYNC_FAILED` includes "
                    "`details.forgejo_sync` with assignee/comment outcomes (`unknown` for ambiguous writes), "
                    "the known comment ID and `reconciliation_required`. Remote effects require operator "
                    "verification when this flag is true; automatic remote reconciliation is not implemented."
                )
            },
        },
        description=(
            "Atomically claims a Work item, then assigns the mapped Forgejo account and posts an Issue comment "
            "containing the claim epoch. The initial assignment preserves assignees read from Forgejo, but "
            "Forgejo's whole-set PATCH has no compare-and-swap protection against concurrent remote edits. "
            "Work and Forgejo do not share a transaction. If synchronization fails, the service rolls back "
            "only the matching local claim. The Work API's current holder, epoch and expiry authorize work; "
            "Forgejo assignees/comments are advisory and may require manual reconciliation."
        ),
    )
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
            claimed = load(db, work_id)
        try:
            sync = sync_forgejo_claim(claimed, actor, app.state.settings)
        except APIError:
            # Release only this exact claim. A concurrent webhook may have fenced it;
            # never overwrite the newer state when cleaning up a failed sync.
            with transaction(database) as db:
                db.execute(
                    "UPDATE work SET status='queued',holder=NULL,expires=0,epoch=epoch+1 "
                    "WHERE id=? AND holder=? AND epoch=? AND status='claimed'",
                    (work_id, actor.sub, epoch),
                )
                current = load(db, work_id)
                audit(db, actor, "work.claim_sync_failed", work_id, current["epoch"])
            raise
        try:
            with transaction(database) as db:
                current = load(db, work_id)
                stale = (
                    current["status"] != "claimed"
                    or current["holder"] != actor.sub
                    or current["epoch"] != epoch
                    or current["expires"] <= time.time()
                )
                if stale:
                    audit(db, actor, "work.claim_sync_stale", work_id, epoch)
            # Remote cleanup must run outside the local transaction so a webhook
            # or new holder can keep progressing. Never alter their local state.
            if stale:
                if sync is not None:
                    sync.discard_comment()
                raise APIError(
                    409, "STALE_CLAIM",
                    "claim changed while Forgejo synchronization was in progress; "
                    "remote side effects may remain (see details)",
                    details=sync.details() if sync is not None else {},
                )
            return response(request, current)
        finally:
            if sync is not None:
                sync.close()

    @app.post("/api/v1/work/{work_id}/renew")
    def renew(work_id: str, body: Renew, request: Request, actor=Depends(scope("dev:write"))):
        """Extend an active claim. Renewal keeps the epoch; expiry or re-claim fences it."""
        with transaction(database) as db:
            item = load(db, work_id)
            if item["holder"] != actor.sub or item["epoch"] != body.epoch or item["expires"] <= time.time():
                raise APIError(409, "STALE_CLAIM", "active holder and current epoch required")
            expires = time.time() + body.ttl_seconds
            db.execute("UPDATE work SET expires=? WHERE id=?", (expires, work_id))
            audit(db, actor, "work.renewed", work_id, body.epoch)
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
        repository_allowed(app.state.settings, body.repository)
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
        repository_allowed(app.state.settings, repository)
        with connect(database) as db:
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM issue_links WHERE repository=? AND (source=? OR target=?) ORDER BY created",
                    (repository, issue_id, issue_id),
                )
            ]
        return response(request, rows)
