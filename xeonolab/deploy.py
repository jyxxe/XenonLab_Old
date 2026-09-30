import json
import os
import subprocess
import time
from datetime import UTC, datetime
from uuid import uuid4

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, response, scope, transaction


class Artifact(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")


class Release(BaseModel):
    commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    artifacts: list[Artifact] = Field(min_length=1)
    migration_manifest: str
    test_report: str = Field(min_length=1)


class Acquire(BaseModel):
    agent_id: str
    release_id: str
    ttl_seconds: int = Field(default=60, ge=15, le=300)


class Renew(BaseModel):
    ttl_seconds: int = Field(default=60, ge=15, le=300)


class Deployment(BaseModel):
    release_id: str
    environment: str = Field(default="production", pattern="^production$")
    lease_id: str
    epoch: int


def create_app(settings=None):
    app = base_app("deploy-gateway", settings)
    path = app.state.settings.data_dir / "deploy.sqlite"
    with transaction(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS releases(id TEXT PRIMARY KEY,manifest TEXT,creator TEXT,approved INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS lease(singleton INTEGER PRIMARY KEY CHECK(singleton=1),id TEXT,holder TEXT,
          release_id TEXT,epoch INTEGER NOT NULL DEFAULT 0,expires REAL);
        INSERT OR IGNORE INTO lease(singleton) VALUES(1);
        CREATE TABLE IF NOT EXISTS deployments(id TEXT PRIMARY KEY,release_id TEXT,holder TEXT,epoch INTEGER,state TEXT);
        CREATE TABLE IF NOT EXISTS audit(id TEXT PRIMARY KEY,actor TEXT,event TEXT,resource TEXT,at REAL);
        """)

    def audit(db, actor, event, resource):
        db.execute(
            "INSERT INTO audit VALUES(?,?,?,?,?)", (str(uuid4()), actor.sub, event, resource, time.time())
        )

    def codex(actor):
        if actor.kind != "codex":
            raise APIError(403, "CODEX_REQUIRED", "only a verified Codex identity may deploy")

    def unresolved(db):
        return db.execute(
            "SELECT 1 FROM deployments WHERE state NOT IN ('completed','rolled_back','cancelled')"
        ).fetchone()

    @app.post("/api/v1/releases", status_code=201)
    def release(body: Release, request: Request, actor=Depends(scope("deploy:release"))):
        rid = str(uuid4())
        with transaction(path) as db:
            db.execute("INSERT INTO releases VALUES(?,?,?,0)", (rid, body.model_dump_json(), actor.sub))
            audit(db, actor, "release.created", rid)
        return response(request, {"release_id": rid, "approved": False})

    @app.post("/api/v1/releases/{rid}/approve")
    def approve(rid: str, request: Request, actor=Depends(scope("deploy:approve"))):
        if actor.kind not in ("human", "policy"):
            raise APIError(403, "HUMAN_REQUIRED", "human or authorized policy approval required")
        with transaction(path) as db:
            if not db.execute("SELECT 1 FROM releases WHERE id=?", (rid,)).fetchone():
                raise APIError(404, "RELEASE_NOT_FOUND", "release not found")
            db.execute("UPDATE releases SET approved=1 WHERE id=?", (rid,))
            audit(db, actor, "release.approved", rid)
        return response(request, {"approved": True})

    @app.get("/api/v1/releases")
    def releases(request: Request, actor=Depends(scope("deploy:read"))):
        with connect(path) as db:
            result = [dict(r) for r in db.execute("SELECT * FROM releases")]
        return response(request, result)

    @app.get("/api/v1/deploy/lease")
    def status(request: Request, actor=Depends(scope("deploy:read"))):
        with connect(path) as db:
            result = dict(db.execute("SELECT * FROM lease").fetchone())
        return response(request, result)

    @app.post("/api/v1/deploy/lease/acquire")
    def acquire(body: Acquire, request: Request, actor=Depends(scope("deploy:execute"))):
        codex(actor)
        if body.agent_id != actor.sub:
            raise APIError(403, "IDENTITY_MISMATCH", "agent_id must match token subject")
        with transaction(path) as db:
            release = db.execute("SELECT approved FROM releases WHERE id=?", (body.release_id,)).fetchone()
            if not release:
                raise APIError(404, "RELEASE_NOT_FOUND", "release not found")
            if not release[0]:
                raise APIError(403, "RELEASE_NOT_APPROVED", "release requires approval")
            current = db.execute("SELECT * FROM lease").fetchone()
            if unresolved(db):
                raise APIError(
                    409, "DEPLOYMENT_UNRESOLVED", "executor must confirm previous deployment ended"
                )
            if current["id"] and current["expires"] > time.time():
                raise APIError(409, "LEASE_BUSY", "a deployment lease is already active")
            lid, epoch, expires = str(uuid4()), current["epoch"] + 1, time.time() + body.ttl_seconds
            db.execute(
                "UPDATE lease SET id=?,holder=?,release_id=?,epoch=?,expires=?",
                (lid, actor.sub, body.release_id, epoch, expires),
            )
            audit(db, actor, "lease.acquired", lid)
        return response(
            request,
            {
                "lease_id": lid,
                "epoch": epoch,
                "holder": actor.sub,
                "expires_at": datetime.fromtimestamp(expires, UTC).isoformat().replace("+00:00", "Z"),
            },
        )

    def check(db, lid, actor, epoch=None):
        current = db.execute("SELECT * FROM lease").fetchone()
        if current["id"] != lid or current["holder"] != actor.sub:
            raise APIError(403, "LEASE_FORBIDDEN", "lease does not belong to caller")
        if current["expires"] <= time.time():
            raise APIError(409, "LEASE_EXPIRED", "lease expired")
        if epoch is not None and current["epoch"] != epoch:
            raise APIError(409, "FENCED", "stale deployment epoch")
        return current

    @app.post("/api/v1/deploy/lease/{lid}/renew")
    def renew(lid: str, body: Renew, request: Request, actor=Depends(scope("deploy:execute"))):
        codex(actor)
        with transaction(path) as db:
            check(db, lid, actor)
            expires = time.time() + body.ttl_seconds
            db.execute("UPDATE lease SET expires=?", (expires,))
            audit(db, actor, "lease.renewed", lid)
        return response(
            request, {"expires_at": datetime.fromtimestamp(expires, UTC).isoformat().replace("+00:00", "Z")}
        )

    @app.post("/api/v1/deploy/lease/{lid}/release")
    def release_lease(lid: str, request: Request, actor=Depends(scope("deploy:execute"))):
        codex(actor)
        with transaction(path) as db:
            check(db, lid, actor)
            if unresolved(db):
                raise APIError(409, "DEPLOYMENT_UNRESOLVED", "deployment is still unresolved")
            db.execute("UPDATE lease SET id=NULL,holder=NULL,release_id=NULL,expires=NULL")
            audit(db, actor, "lease.released", lid)
        return response(request, {"released": True})

    @app.post("/api/v1/deployments")
    def deploy(body: Deployment, request: Request, actor=Depends(scope("deploy:execute"))):
        codex(actor)
        with transaction(path) as db:
            current = check(db, body.lease_id, actor, body.epoch)
            if current["release_id"] != body.release_id:
                raise APIError(409, "RELEASE_MISMATCH", "lease is bound to another release")
            if unresolved(db):
                raise APIError(409, "DEPLOYMENT_UNRESOLVED", "deployment already running")
            executor = os.getenv("DEPLOY_EXECUTOR_PATH")
            if not executor:
                raise APIError(
                    503, "EXECUTOR_NOT_CONFIGURED", "production executor must be installed before deployments"
                )
            did = str(uuid4())
            manifest = json.loads(
                db.execute("SELECT manifest FROM releases WHERE id=?", (body.release_id,)).fetchone()[0]
            )
            db.execute(
                "INSERT INTO deployments VALUES(?,?,?,?,?)",
                (did, body.release_id, actor.sub, body.epoch, "deploying"),
            )
            audit(db, actor, "deployment.started", did)
        state = "failed"
        try:
            result = subprocess.run(
                [executor],
                input=json.dumps({"manifest": manifest, "deployment_id": did, "epoch": body.epoch}),
                text=True,
                capture_output=True,
                timeout=600,
            )
            if result.returncode == 0:
                candidate = json.loads(result.stdout).get("state")
                if candidate in ("completed", "rolled_back"):
                    state = candidate
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        with transaction(path) as db:
            db.execute("UPDATE deployments SET state=? WHERE id=?", (state, did))
            audit(db, actor, "deployment." + state, did)
        return response(request, {"deployment_id": did, "state": state})

    @app.get("/api/v1/deployments/{did}")
    def deployment(did: str, request: Request, actor=Depends(scope("deploy:read"))):
        with connect(path) as db:
            row = db.execute("SELECT * FROM deployments WHERE id=?", (did,)).fetchone()
        if not row:
            raise APIError(404, "DEPLOYMENT_NOT_FOUND", "deployment not found")
        return response(request, dict(row))

    @app.get("/api/v1/audit/events")
    def audit_events(request: Request, actor=Depends(scope("deploy:read"))):
        with connect(path) as db:
            rows = db.execute(
                "SELECT * FROM audit WHERE actor=? ORDER BY at DESC LIMIT 100", (actor.sub,)
            ).fetchall()
        return response(request, [dict(r) for r in rows])

    return app
