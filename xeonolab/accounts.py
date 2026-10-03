"""Account trees and centrally enforced, revocable API keys."""

import base64
import binascii
import hashlib
import json
import math
import os
import secrets
import ssl
import time
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, Identity, connect, identity, response, token_identity, transaction
from .oauth import service_token

SCOPES = {
    "accounts:manage",
    "desktop:read",
    "desktop:admin",
    "dev:read",
    "dev:write",
    "xelibrary:read",
    "xelibrary:write",
    "xelibrary:query",
    "xelibrary:attach",
    "xelibrary:admin",
    "xelibrary:theorem:read",
    "xelibrary:theorem:propose",
    "xelibrary:theorem:promote",
    "embedding:read",
    "embedding:write",
    "hf-cache:read",
    "hf-cache:write",
    "hf-cache:admin",
    "llm:use",
    "deploy:read",
    "deploy:release",
    "deploy:approve",
    "deploy:execute",
}


class Bootstrap(BaseModel):
    name: str = Field(default="管理员", min_length=1, max_length=80)


class Child(BaseModel):
    parent_id: str
    username: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{2,63}$")
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=12, max_length=256)
    inherit_all: bool = False
    grants: list[str] = Field(default_factory=list, max_length=64)


class Update(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    enabled: bool | None = None
    inherit_all: bool | None = None
    grants: list[str] | None = Field(default=None, max_length=64)


class Password(BaseModel):
    password: str = Field(min_length=12, max_length=256)


class KeyCreate(BaseModel):
    owner: str
    name: str = Field(min_length=1, max_length=80)
    scopes: list[str] = Field(max_length=64)


def audit_cursor(at: float, event_id: str) -> str:
    value = json.dumps([at, event_id], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def audit_boundary(cursor: str) -> tuple[float, str]:
    try:
        if len(cursor) > 512:
            raise ValueError
        value = json.loads(base64.b64decode(cursor + "=" * (-len(cursor) % 4), altchars=b"-_", validate=True))
        if not isinstance(value, list) or len(value) != 2:
            raise ValueError
        at, event_id = value
        if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at):
            raise ValueError
        if not isinstance(event_id, str) or not 1 <= len(event_id) <= 128:
            raise ValueError
        return float(at), event_id
    except (ValueError, TypeError, OverflowError, binascii.Error):
        raise APIError(422, "INVALID_CURSOR", "invalid account audit cursor") from None


class Keycloak:
    def request(self, method, suffix, body=None):
        base = os.getenv("ACCOUNT_ADMIN_URL")
        if not base:
            raise APIError(503, "ACCOUNT_PROVIDER_NOT_CONFIGURED", "account provisioning unavailable")
        try:
            context = ssl.create_default_context(cafile=os.getenv("OIDC_CA_FILE"))
            with httpx.Client(verify=context, trust_env=False, timeout=20) as client:
                result = client.request(
                    method,
                    base + suffix,
                    json=body,
                    headers={"Authorization": "Bearer " + service_token("ACCOUNT_AUTH")},
                )
            if result.status_code == 409:
                raise APIError(409, "USERNAME_EXISTS", "username already exists")
            result.raise_for_status()
            return result
        except (httpx.HTTPError, OSError):
            raise APIError(
                503, "ACCOUNT_PROVIDER_UNAVAILABLE", "identity account operation unavailable"
            ) from None

    def create(self, body):
        result = self.request(
            "POST",
            "/users",
            {
                "username": body.username,
                "enabled": True,
                "firstName": body.name,
                "lastName": "XeonoLab",
                "email": body.username + "@xenon.lan",
                "emailVerified": True,
                "credentials": [{"type": "password", "value": body.password, "temporary": False}],
            },
        )
        uid = str(UUID(result.headers["Location"].rsplit("/", 1)[1]))
        try:
            role = self.request("GET", "/roles/offline_access").json()
            self.request("POST", "/users/" + uid + "/role-mappings/realm", [role])
        except BaseException:
            self.delete(uid)
            raise
        return uid

    def delete(self, uid):
        self.request("DELETE", "/users/" + str(UUID(uid)))

    def password(self, uid, value, epoch):
        user = self.request("GET", "/users/" + str(UUID(uid))).json()
        attributes = user.get("attributes", {})
        attributes["xeonolab_credential_epoch"] = [epoch]
        user["attributes"] = attributes
        self.request("PUT", "/users/" + str(UUID(uid)), user)
        self.request(
            "PUT",
            "/users/" + str(UUID(uid)) + "/reset-password",
            {
                "type": "password",
                "value": value,
                "temporary": False,
            },
        )

        self.request("POST", "/users/" + str(UUID(uid)) + "/logout")


def install(app):
    path = app.state.settings.data_dir / "accounts.sqlite"
    with transaction(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS accounts(
          id TEXT PRIMARY KEY, parent TEXT REFERENCES accounts(id), username TEXT UNIQUE NOT NULL,
          name TEXT NOT NULL, kind TEXT NOT NULL, enabled INTEGER NOT NULL,
          inherit_all INTEGER NOT NULL, grants TEXT NOT NULL, not_before REAL NOT NULL DEFAULT 0,
          created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS api_keys(
          id TEXT PRIMARY KEY, owner TEXT NOT NULL REFERENCES accounts(id), name TEXT NOT NULL,
          digest TEXT UNIQUE NOT NULL, scopes TEXT NOT NULL, created REAL NOT NULL,
          revoked INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS account_audit(
          id TEXT PRIMARY KEY, actor TEXT NOT NULL, event TEXT NOT NULL,
          target TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS account_audit_order ON account_audit(at DESC, id DESC);
        """)
    with transaction(path) as db:
        if "credential_epoch" not in {row[1] for row in db.execute("PRAGMA table_info(accounts)")}:
            db.execute("ALTER TABLE accounts ADD COLUMN credential_epoch TEXT NOT NULL DEFAULT ''")
    path.chmod(0o600)
    app.state.account_provider = Keycloak()

    def load(db, uid):
        row = db.execute("SELECT * FROM accounts WHERE id=?", (uid,)).fetchone()
        if not row:
            raise APIError(404, "ACCOUNT_NOT_FOUND", "account not registered")
        return row

    def chain(db, uid):
        result, seen = [], set()
        while uid:
            if uid in seen or len(seen) >= 128:
                raise APIError(503, "INVALID_ACCOUNT_TREE", "account tree cannot be resolved")
            seen.add(uid)
            row = load(db, uid)
            result.append(row)
            uid = row["parent"]
        return result

    def effective(db, uid):
        nodes = chain(db, uid)
        grants = set()
        for node in reversed(nodes):
            if not node["enabled"]:
                return set()
            requested = set(json.loads(node["grants"]))
            if node["parent"] is None:
                grants = requested
            elif not node["inherit_all"]:
                grants &= requested
        return grants

    def owned(db, actor, target):
        if actor.sub not in {row["id"] for row in chain(db, target)}:
            raise APIError(403, "OUTSIDE_ACCOUNT_TREE", "only your account subtree is accessible")

    def interactive(request):
        if request.headers.get("Authorization", "").startswith("Bearer xek_"):
            raise APIError(
                403, "INTERACTIVE_LOGIN_REQUIRED", "interactive OIDC login required for account changes"
            )

    def manage(actor):
        if actor.kind != "human" or not ({"accounts:manage", "desktop:admin"} & actor.scopes):
            raise APIError(403, "ACCOUNT_MANAGEMENT_REQUIRED", "account management permission required")

    def validate(grants, available):
        if not set(grants) <= SCOPES or not set(grants) <= available:
            raise APIError(403, "PERMISSION_ESCALATION", "grants must be a subset of inherited permissions")

    def request_scopes(actor):
        # Keep validated token claims distinct from active account grants so
        # a root can explicitly adopt a new OIDC role scope. Child managers
        # remain limited to their delegated effective account scopes.
        if not actor.ancestors and actor.token_scopes is not None:
            return actor.token_scopes
        return actor.scopes

    def audit(db, actor, event, target):
        db.execute(
            "INSERT INTO account_audit VALUES(?,?,?,?,?)",
            (str(uuid4()), actor.sub, event, target, time.time()),
        )

    def resolve(token):
        with connect(path) as db:
            if token.startswith("xek_"):
                row = db.execute(
                    "SELECT * FROM api_keys WHERE digest=?", (hashlib.sha256(token.encode()).hexdigest(),)
                ).fetchone()
                if not row or row["revoked"]:
                    raise APIError(401, "INVALID_API_KEY", "API key invalid or revoked")
                account = load(db, row["owner"])
                if row["created"] < account["not_before"]:
                    raise APIError(401, "CREDENTIAL_REVOKED", "credentials revoked")
                actor = Identity(
                    account["id"], set(json.loads(row["scopes"])), account["kind"], row["created"]
                )
            else:
                actor = token_identity(token, app.state.settings)
                account = db.execute("SELECT * FROM accounts WHERE id=?", (actor.sub,)).fetchone()
                if not account:
                    return actor  # Existing operator-provisioned OIDC identities remain valid.
                stale = (
                    actor.credential_epoch != account["credential_epoch"]
                    if account["credential_epoch"]
                    else actor.issued_at < account["not_before"]
                )
                if actor.kind != account["kind"] or stale:
                    raise APIError(401, "CREDENTIAL_REVOKED", "credentials revoked; sign in again")
            if not all(node["enabled"] for node in chain(db, actor.sub)):
                raise APIError(401, "ACCOUNT_DISABLED", "account or ancestor disabled")
            permissions = effective(db, actor.sub)
            if token.startswith("xek_"):
                permissions &= actor.scopes
            return Identity(
                actor.sub,
                permissions,
                actor.kind,
                actor.issued_at,
                actor.credential_epoch,
                tuple(row["id"] for row in chain(db, actor.sub)[1:]),
                # Only token_identity's verified OIDC claims are candidate
                # grants. API-key scopes are database policy, not JWT claims.
                token_scopes=None if token.startswith("xek_") else actor.token_scopes,
            )

    app.state.authorization_resolver = resolve

    def view(db, row):
        data = dict(row)
        data["grants"] = json.loads(data["grants"])
        data["effective_scopes"] = sorted(effective(db, row["id"]))
        data["enabled"], data["inherit_all"] = bool(row["enabled"]), bool(row["inherit_all"])
        return data

    @app.get("/api/v1/auth/authorization")
    def authorization(request: Request, actor=Depends(identity)):
        result = response(
            request,
            {
                "subject": actor.sub,
                "kind": actor.kind,
                "scopes": sorted(actor.scopes),
                "ancestors": actor.ancestors,
            },
        )
        return result

    @app.post("/api/v1/auth/accounts/bootstrap", status_code=201)
    def bootstrap(body: Bootstrap, request: Request, actor=Depends(identity)):
        interactive(request)
        actor.require("desktop:admin")
        if actor.kind != "human":
            raise APIError(403, "HUMAN_REQUIRED", "human root account required")
        with transaction(path) as db:
            if db.execute("SELECT 1 FROM accounts WHERE id=?", (actor.sub,)).fetchone():
                raise APIError(409, "ACCOUNT_EXISTS", "account already registered")
            db.execute(
                "INSERT INTO accounts(id,parent,username,name,kind,enabled,inherit_all,grants,not_before,created) VALUES(?,NULL,?,?,?,?,?,?,0,?)",
                (
                    actor.sub,
                    actor.sub,
                    body.name,
                    "human",
                    1,
                    0,
                    json.dumps(sorted(actor.scopes & SCOPES)),
                    time.time(),
                ),
            )
            audit(db, actor, "account.bootstrap", actor.sub)
            return response(request, view(db, load(db, actor.sub)))

    @app.get("/api/v1/auth/accounts")
    def tree(request: Request, cursor: str = "", limit: int = 100, actor=Depends(identity)):
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit must be 1..1000")
        with connect(path) as db:
            load(db, actor.sub)
            rows = db.execute(
                """WITH RECURSIVE children(id) AS (
                SELECT ? UNION SELECT a.id FROM accounts a JOIN children c ON a.parent=c.id)
                SELECT a.* FROM accounts a JOIN children c ON c.id=a.id
                WHERE a.id>? ORDER BY a.id LIMIT ?""",
                (actor.sub, cursor, limit + 1),
            ).fetchall()
            return response(
                request,
                [view(db, row) for row in rows[:limit]],
                next_cursor=rows[limit - 1]["id"] if len(rows) > limit else None,
                scope_catalog=sorted(SCOPES),
            )

    @app.get("/api/v1/auth/accounts/root-scope-candidates")
    def root_scope_candidates(request: Request, actor=Depends(identity)):
        # This endpoint reveals only what this signed-in human root could
        # explicitly add to their own root grants. It is not delegated to
        # account managers or API keys and does not activate any permission.
        if actor.kind != "human" or actor.token_scopes is None:
            raise APIError(403, "SIGNED_HUMAN_ROOT_REQUIRED", "signed-in human root required")
        with connect(path) as db:
            row = load(db, actor.sub)
            if row["parent"] is not None:
                raise APIError(403, "SIGNED_HUMAN_ROOT_REQUIRED", "signed-in human root required")
            candidates = (actor.token_scopes & SCOPES) - effective(db, actor.sub)
            return response(request, {"candidate_scopes": sorted(candidates)})

    @app.post("/api/v1/auth/accounts", status_code=201)
    def create(body: Child, request: Request, actor=Depends(identity)):
        interactive(request)
        manage(actor)
        with transaction(path) as db:
            owned(db, actor, body.parent_id)
            parent = load(db, body.parent_id)
            if len(chain(db, body.parent_id)) >= 128 or parent["kind"] != "human":
                raise APIError(422, "INVALID_PARENT", "human parent and maximum depth 128 required")
            if not all(node["enabled"] for node in chain(db, body.parent_id)):
                raise APIError(409, "PARENT_DISABLED", "parent disabled")
            validate(body.grants, effective(db, body.parent_id) & request_scopes(actor))
            if body.inherit_all:
                validate(effective(db, body.parent_id), request_scopes(actor))
            if db.execute(
                "SELECT 1 FROM accounts WHERE lower(username)=lower(?)", (body.username,)
            ).fetchone():
                raise APIError(409, "USERNAME_EXISTS", "username already exists")
            uid = app.state.account_provider.create(body)
            try:
                db.execute(
                    "INSERT INTO accounts(id,parent,username,name,kind,enabled,inherit_all,grants,not_before,created) VALUES(?,?,?,?,?,?,?,?,0,?)",
                    (
                        uid,
                        body.parent_id,
                        body.username,
                        body.name,
                        "human",
                        1,
                        body.inherit_all,
                        json.dumps(sorted(set(body.grants))),
                        time.time(),
                    ),
                )
                audit(db, actor, "account.created", uid)
                return response(request, view(db, load(db, uid)))
            except BaseException:
                app.state.account_provider.delete(uid)
                raise

    @app.patch("/api/v1/auth/accounts/{uid}")
    def update(uid: str, body: Update, request: Request, actor=Depends(identity)):
        interactive(request)
        manage(actor)
        with transaction(path) as db:
            owned(db, actor, uid)
            row = load(db, uid)
            policy = body.enabled is not None or body.inherit_all is not None or body.grants is not None
            if uid == actor.sub and row["parent"] and policy:
                raise APIError(403, "PARENT_REQUIRED", "only an ancestor can change your inherited policy")
            grants = body.grants if body.grants is not None else json.loads(row["grants"])
            # Existing grants may become ineffective after parent permissions shrink.
            if body.grants is not None:
                validate(
                    grants,
                    (effective(db, row["parent"]) if row["parent"] else request_scopes(actor))
                    & request_scopes(actor),
                )
            if row["parent"] is None and (
                body.enabled is False or not {"desktop:read", "desktop:admin"} <= set(grants)
            ):
                raise APIError(
                    409, "ROOT_ACCOUNT_REQUIRED", "root account must retain desktop administration"
                )
            db.execute(
                "UPDATE accounts SET name=?,enabled=?,inherit_all=?,grants=? WHERE id=?",
                (
                    body.name if body.name is not None else row["name"],
                    body.enabled if body.enabled is not None else row["enabled"],
                    body.inherit_all if body.inherit_all is not None else row["inherit_all"],
                    json.dumps(sorted(set(grants))),
                    uid,
                ),
            )
            audit(db, actor, "account.updated", uid)
            return response(request, view(db, load(db, uid)))

    @app.post("/api/v1/auth/accounts/{uid}/password")
    def password(uid: str, body: Password, request: Request, actor=Depends(identity)):
        interactive(request)
        if actor.kind != "human":
            raise APIError(403, "HUMAN_REQUIRED", "human login required for password changes")
        if uid != actor.sub:
            manage(actor)
        # Fence old credentials before a potentially ambiguous provider timeout.
        epoch = str(uuid4())
        with transaction(path) as db:
            owned(db, actor, uid)
            db.execute(
                "UPDATE accounts SET not_before=?,credential_epoch=? WHERE id=?", (time.time(), epoch, uid)
            )
            db.execute("UPDATE api_keys SET revoked=1 WHERE owner=?", (uid,))
            audit(db, actor, "account.password_reset_requested", uid)
        sessions = app.state.settings.data_dir / "browser-sessions.sqlite"
        with transaction(sessions) as db:
            db.execute("DELETE FROM sessions WHERE subject=?", (uid,))
        try:
            app.state.account_provider.password(uid, body.password, epoch)
        except APIError:
            raise APIError(
                503, "PASSWORD_RESET_PENDING", "旧凭据已撤销，身份同步尚未完成；请由父账号或运维管理员重试"
            ) from None
        with transaction(path) as db:
            audit(db, actor, "account.password_reset", uid)
        return response(request, {"reset": True, "sessions_revoked": True, "api_keys_revoked": True})

    @app.get("/api/v1/auth/api-keys")
    def keys(owner: str, request: Request, actor=Depends(identity)):
        with connect(path) as db:
            owned(db, actor, owner)
            rows = db.execute(
                "SELECT id,owner,name,scopes,created,revoked FROM api_keys WHERE owner=? ORDER BY created",
                (owner,),
            ).fetchall()
            return response(request, [{**dict(row), "scopes": json.loads(row["scopes"])} for row in rows])

    @app.post("/api/v1/auth/api-keys", status_code=201)
    def key(body: KeyCreate, request: Request, actor=Depends(identity)):
        if body.owner != actor.sub:
            manage(actor)
        token = "xek_" + secrets.token_urlsafe(48)
        kid = str(uuid4())
        with transaction(path) as db:
            owned(db, actor, body.owner)
            validate(body.scopes, effective(db, body.owner) & actor.scopes)
            db.execute(
                "INSERT INTO api_keys VALUES(?,?,?,?,?,?,0)",
                (
                    kid,
                    body.owner,
                    body.name,
                    hashlib.sha256(token.encode()).hexdigest(),
                    json.dumps(sorted(set(body.scopes))),
                    time.time(),
                ),
            )
            audit(db, actor, "api_key.created:" + kid, body.owner)
        from fastapi.responses import JSONResponse

        return JSONResponse(
            response(request, {"id": kid, "token": token, "scopes": body.scopes}),
            status_code=201,
            headers={"Cache-Control": "no-store"},
        )

    @app.delete("/api/v1/auth/api-keys/{kid}")
    def revoke(kid: str, request: Request, actor=Depends(identity)):
        with transaction(path) as db:
            row = db.execute("SELECT owner FROM api_keys WHERE id=?", (kid,)).fetchone()
            if not row:
                raise APIError(404, "API_KEY_NOT_FOUND", "key not found")
            owned(db, actor, row["owner"])
            if row["owner"] != actor.sub:
                manage(actor)
            db.execute("UPDATE api_keys SET revoked=1 WHERE id=?", (kid,))
            audit(db, actor, "api_key.revoked:" + kid, row["owner"])
        return response(request, {"revoked": True})

    @app.get("/api/v1/auth/accounts/audit/events")
    def events(request: Request, cursor: str = "", limit: int = 200, actor=Depends(identity)):
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit must be 1..1000")
        boundary = audit_boundary(cursor) if cursor else None
        clause = " AND (e.at < ? OR (e.at = ? AND e.id < ?))" if boundary else ""
        parameters = (boundary[0], boundary[0], boundary[1]) if boundary else ()
        with connect(path) as db:
            rows = db.execute(
                """WITH RECURSIVE children(id) AS (
                SELECT ? UNION SELECT a.id FROM accounts a JOIN children c ON a.parent=c.id)
                SELECT e.* FROM account_audit e WHERE (e.actor IN (SELECT id FROM children)
                OR e.target IN (SELECT id FROM children))""" + clause +
                " ORDER BY e.at DESC, e.id DESC LIMIT ?",
                (actor.sub, *parameters, limit + 1),
            ).fetchall()
            return response(
                request,
                [dict(row) for row in rows[:limit]],
                next_cursor=audit_cursor(rows[limit - 1]["at"], rows[limit - 1]["id"])
                if len(rows) > limit else None,
            )
