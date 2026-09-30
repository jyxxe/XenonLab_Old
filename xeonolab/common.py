import hashlib
import json
import logging
import math
import os
import sqlite3
import ssl
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException


class APIError(Exception):
    def __init__(self, status, code, message, headers=None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}


DEFAULT_RATE_LIMITS = {
    "interactive": {"requests": 120, "window_seconds": 60},
    "bulk": {"requests": 10, "window_seconds": 60},
    "admin": {"requests": 60, "window_seconds": 60},
    "agent": {"requests": 60, "window_seconds": 60},
    "embedding-fast": {"requests": 60, "window_seconds": 60},
    "embedding-quality": {"requests": 6, "window_seconds": 60},
}


@dataclass
class Identity:
    sub: str
    scopes: set[str]
    kind: str = "human"
    issued_at: float = 0
    credential_epoch: str = ""
    ancestors: tuple[str, ...] = ()

    def require(self, scope):
        if scope not in self.scopes:
            raise APIError(403, "FORBIDDEN", f"scope required: {scope}")


@dataclass
class Settings:
    data_dir: Path
    mode: str = "production"
    issuer: str = ""
    audience: str = "xeonolab"
    public_key: str = ""
    dev_tokens: dict | None = None
    role_scopes: dict | None = None
    rate_limit_enabled: bool = True
    rate_limit_policies: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_RATE_LIMITS)))

    @classmethod
    def environment(cls, service):
        policies = json.loads(json.dumps(DEFAULT_RATE_LIMITS))
        overrides = json.loads(os.getenv("RATE_LIMIT_POLICIES_JSON", "{}"))
        if not isinstance(overrides, dict):
            raise ValueError("RATE_LIMIT_POLICIES_JSON must be a JSON object")
        for group, override in overrides.items():
            if group not in policies or not isinstance(override, dict):
                raise ValueError(f"invalid rate-limit policy group: {group}")
            policies[group].update(override)
        return cls(
            Path(os.getenv("DATA_DIR", f"runtime/{service}")),
            os.getenv("APP_MODE", "production"),
            os.getenv("OIDC_ISSUER", ""),
            os.getenv("OIDC_AUDIENCE", "xeonolab"),
            (
                Path(os.environ["OIDC_PUBLIC_KEY_FILE"]).read_text()
                if os.getenv("OIDC_PUBLIC_KEY_FILE")
                else os.getenv("OIDC_PUBLIC_KEY", "")
            ),
            json.loads(os.getenv("DEV_TOKENS_JSON", "{}")),
            json.loads(os.getenv("OIDC_ROLE_SCOPES_JSON", "{}")),
            os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
            policies,
        )


def token_identity(token, settings):
    if settings.mode == "development" and token in (settings.dev_tokens or {}):
        claims = settings.dev_tokens[token]
    else:
        if not settings.public_key or not settings.issuer:
            raise APIError(503, "AUTH_NOT_CONFIGURED", "OIDC verification not configured")
        try:
            claims = jwt.decode(
                token,
                settings.public_key,
                algorithms=["RS256"],
                issuer=settings.issuer,
                audience=settings.audience,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            raise APIError(401, "INVALID_TOKEN", "invalid access token") from None
    scopes = set(claims.get("scope", "").split()) | set(claims.get("platform_scopes", "").split())
    for role in claims.get("realm_access", {}).get("roles", []):
        scopes.update((settings.role_scopes or {}).get(role, []))
    return Identity(
        claims["sub"],
        scopes,
        claims.get("agent_kind", "human"),
        claims.get("iat", 0),
        claims.get("account_epoch", ""),
    )


def identity(request: Request):
    settings = request.app.state.settings
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise APIError(401, "UNAUTHENTICATED", "bearer token required")
    token = header[7:]
    resolver = getattr(request.app.state, "authorization_resolver", None)
    if resolver:
        actor = resolver(token)
        enforce_rate_limit(request, actor)
        return actor
    endpoint = os.getenv("AUTHORIZATION_URL")
    if endpoint:
        # JWTs retain local issuer/signature validation. Opaque keys are verified
        # only by the fixed, TLS-authenticated account authority.
        local = None if token.startswith("xek_") else token_identity(token, settings)
        try:
            context = ssl.create_default_context(cafile=os.getenv("OIDC_CA_FILE"))
            with httpx.Client(verify=context, trust_env=False, timeout=10) as client:
                result = client.get(endpoint, headers={"Authorization": header})
            if result.status_code in (401, 403):
                raise APIError(result.status_code, "AUTHORIZATION_REVOKED", "identity or permission revoked")
            result.raise_for_status()
            data = result.json()["data"]
            if local and (data["subject"] != local.sub or data["kind"] != local.kind):
                raise ValueError("identity authority mismatch")
            actor = Identity(
                data["subject"], set(data["scopes"]), data["kind"], ancestors=tuple(data.get("ancestors", []))
            )
            enforce_rate_limit(request, actor)
            return actor
        except (httpx.HTTPError, ValueError, KeyError, OSError):
            raise APIError(503, "AUTHORIZATION_UNAVAILABLE", "account authority unavailable") from None
    if token.startswith("xek_"):
        raise APIError(503, "AUTHORIZATION_NOT_CONFIGURED", "account authority required for API keys")
    actor = token_identity(token, settings)
    enforce_rate_limit(request, actor)
    return actor


def route_group(request, actor):
    path = request.url.path.lower()
    if "/embedding/quality" in path or path.endswith("/search/semantic"):
        return "embedding-quality"
    if "/embedding/" in path or "/embed/" in path or "/embeddings/" in path or path.endswith("/embeddings"):
        return "embedding-fast"
    if any(segment in path for segment in ("/bulk", "/import", "/export", "/index")):
        return "bulk"
    if any(
        path.startswith(prefix)
        for prefix in (
            "/api/v1/auth/accounts",
            "/api/v1/deploy",
            "/api/v1/deployments",
            "/api/v1/releases",
            "/api/v1/acl",
            "/api/v1/audit",
            "/api/v1/apps",
        )
    ) or path.endswith("/schema"):
        return "admin"
    return "agent" if actor.kind in {"agent", "codex", "opencode"} else "interactive"


def enforce_rate_limit(request, actor):
    settings = request.app.state.settings
    versioned_api = request.url.path.startswith(("/api/v1/", "/v1/"))
    if not settings.rate_limit_enabled or not versioned_api:
        return
    group = route_group(request, actor)
    policy = settings.rate_limit_policies.get(group)
    if policy is None:
        return
    try:
        limit = int(policy["requests"])
        window = float(policy["window_seconds"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(f"invalid rate-limit policy for group {group}") from None
    if limit <= 0 or not math.isfinite(window) or window <= 0:
        raise RuntimeError(f"invalid rate-limit policy for group {group}")

    now = time.time()
    cutoff = now - window
    identity_key = hashlib.sha256(actor.sub.encode("utf-8")).hexdigest()
    store = request.app.state.rate_limit_db
    with transaction(store) as db:
        db.execute("DELETE FROM rate_limit_requests WHERE created_at<=?", (cutoff,))
        recent = db.execute(
            "SELECT created_at FROM rate_limit_requests WHERE identity_key=? AND route_group=? "
            "AND created_at>? ORDER BY created_at LIMIT ?",
            (identity_key, group, cutoff, limit),
        ).fetchall()
        if len(recent) >= limit:
            retry_after = max(1, math.ceil(float(recent[0]["created_at"]) + window - now))
        else:
            db.execute(
                "INSERT INTO rate_limit_requests(identity_key,route_group,created_at) VALUES(?,?,?)",
                (identity_key, group, now),
            )
            return

    logging.getLogger("xeonolab.audit").warning(
        json.dumps(
            {
                "event": "rate_limit.rejected",
                "request_id": getattr(request.state, "request_id", ""),
                "identity_hash": identity_key,
                "route_group": group,
                "route": request.url.path,
                "limit": limit,
                "window_seconds": window,
                "retry_after": retry_after,
            },
            separators=(",", ":"),
        )
    )
    raise APIError(429, "RATE_LIMITED", "request rate limit exceeded", {"Retry-After": str(retry_after)})


def scope(name):
    def dependency(actor: Identity = Depends(identity)):
        actor.require(name)
        return actor

    return dependency


def response(request, data, **meta):
    return {"data": data, "meta": {"request_id": request.state.request_id, **meta}}


def base_app(service, settings=None):
    app = FastAPI(title=f"XeonoLab {service}", version="0.1.0")
    app.state.settings = settings or Settings.environment(service)
    if app.state.settings.rate_limit_enabled:
        for group in DEFAULT_RATE_LIMITS:
            policy = app.state.settings.rate_limit_policies.get(group)
            try:
                limit, window = int(policy["requests"]), float(policy["window_seconds"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"invalid rate-limit policy for group {group}") from None
            if limit <= 0 or not math.isfinite(window) or window <= 0:
                raise ValueError(f"invalid rate-limit policy for group {group}")
        if set(app.state.settings.rate_limit_policies) - set(DEFAULT_RATE_LIMITS):
            raise ValueError("unknown rate-limit policy group configured")
    app.state.settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.requests = 0
    app.state.errors = 0
    app.state.rate_limit_db = app.state.settings.data_dir / "rate-limits.sqlite3"
    with connect(app.state.rate_limit_db) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS rate_limit_requests("
            "identity_key TEXT NOT NULL, route_group TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS rate_limit_window_idx "
            "ON rate_limit_requests(identity_key,route_group,created_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS rate_limit_expiry_idx ON rate_limit_requests(created_at)"
        )

    @app.middleware("http")
    async def context(request, call_next):
        request.state.request_id = str(uuid4())
        supplied = request.headers.get("X-Request-ID", "")
        try:
            from uuid import UUID

            request.state.request_id = str(UUID(supplied))
        except ValueError:
            pass
        started = time.monotonic()
        result = await call_next(request)
        result.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/v1/auth/"):
            result.headers["Cache-Control"] = "no-store"
        app.state.requests += 1
        app.state.errors += result.status_code >= 400
        logging.getLogger("xeonolab").info(
            json.dumps(
                {
                    "service": service,
                    "request_id": request.state.request_id,
                    "event": "request.completed",
                    "status": result.status_code,
                    "duration_ms": round((time.monotonic() - started) * 1000, 2),
                }
            )
        )
        return result

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": {},
                    "request_id": request.state.request_id,
                }
            },
            status_code=exc.status,
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return await api_error(request, APIError(422, "VALIDATION_ERROR", "invalid request fields"))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return await api_error(request, APIError(exc.status_code, "HTTP_ERROR", str(exc.detail)))

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return await api_error(request, APIError(500, "INTERNAL_ERROR", "internal service error"))

    @app.get("/health/live")
    def live():
        return {"status": "live", "service": service}

    @app.get("/health/ready")
    def ready():
        check = getattr(app.state, "ready_check", lambda: True)
        if not check():
            raise APIError(503, "NOT_READY", "required dependencies are unavailable")
        return {"status": "ready", "service": service}

    @app.get("/version")
    def version():
        return {"service": service, "version": "0.1.0"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        return (
            f'xeonolab_requests_total{{service="{service}"}} {app.state.requests}\n'
            f'xeonolab_errors_total{{service="{service}"}} {app.state.errors}\n'
        )

    return app


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path):
    db = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


@contextmanager
def transaction(path):
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def etag(revision):
    return f'"revision:{revision}"'


def precondition(request, revision):
    if request.headers.get("If-Match") != etag(revision):
        raise APIError(412, "STALE_REVISION", "If-Match must equal the current ETag")
