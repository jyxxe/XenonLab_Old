"""Persistent browser sessions; refresh credentials never leave the server."""

import hashlib
import os
import secrets
import ssl
import time
from urllib.parse import urlsplit

import httpx
import jwt
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .common import APIError, transaction

COOKIE = "__Host-xeonolab-session"
ORIGINS = {"https://xenon.lan", "https://xenon.jyxxe.com", "https://192.168.0.101:9443"}


class Exchange(BaseModel):
    code: str = Field(min_length=1, max_length=4096)
    verifier: str = Field(min_length=43, max_length=128)
    redirect: str = Field(min_length=1, max_length=2048)


def _token_endpoint(app, issuer):
    """Resolve an operator-configured endpoint for one exact, allowlisted issuer."""
    if issuer not in app.state.settings.allowed_issuers:
        raise APIError(503, "AUTH_NOT_CONFIGURED", "token endpoint unavailable")
    configured = app.state.settings.oidc_token_urls.get(issuer)
    if configured is None and issuer == app.state.settings.issuer:
        configured = os.getenv("OIDC_TOKEN_URL")
    if configured is None:
        configured = issuer.rstrip("/") + "/protocol/openid-connect/token"
    parts = urlsplit(configured)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or parts.hostname != urlsplit(issuer).hostname
        or parts.port != urlsplit(issuer).port
        or parts.path != urlsplit(issuer).path.rstrip("/") + "/protocol/openid-connect/token"
    ):
        raise APIError(503, "AUTH_NOT_CONFIGURED", "token endpoint unavailable")
    return configured


def _issuer_for_origin(settings, value):
    if value not in ORIGINS:
        raise APIError(403, "INVALID_ORIGIN", "same-origin browser request required")
    issuer = settings.oidc_issuers_by_origin.get(value, settings.issuer)
    if issuer not in settings.allowed_issuers or not issuer.startswith("https://"):
        raise APIError(503, "AUTH_NOT_CONFIGURED", "identity issuer unavailable")
    return issuer


def _oidc_ssl_context():
    """Use platform trust roots and add the configured local CA when present."""
    context = ssl.create_default_context()
    ca_file = os.getenv("OIDC_CA_FILE")
    if ca_file:
        context.load_verify_locations(cafile=ca_file)
    return context


def install(app):
    path = app.state.settings.data_dir / "browser-sessions.sqlite"
    with transaction(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY, subject TEXT NOT NULL, refresh TEXT NOT NULL,
            access TEXT NOT NULL, expires REAL NOT NULL)""")
        db.execute("""CREATE TABLE IF NOT EXISTS session_issuers(
            session_id TEXT PRIMARY KEY, issuer TEXT NOT NULL)""")
        session_columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)")}
        if "issuer" in session_columns:
            # Collapse the short-lived six-column schema from the prior local
            # release while preserving its per-session issuers.
            db.execute(
                """INSERT OR IGNORE INTO session_issuers(session_id,issuer)
                   SELECT id,COALESCE(NULLIF(issuer,''),?) FROM sessions""",
                (app.state.settings.issuer,),
            )
            db.execute("ALTER TABLE sessions RENAME TO sessions_with_issuer")
            db.execute("""CREATE TABLE sessions(
                id TEXT PRIMARY KEY, subject TEXT NOT NULL, refresh TEXT NOT NULL,
                access TEXT NOT NULL, expires REAL NOT NULL)""")
            db.execute("""INSERT INTO sessions(id,subject,refresh,access,expires)
                          SELECT id,subject,refresh,access,expires FROM sessions_with_issuer""")
            db.execute("DROP TABLE sessions_with_issuer")
        db.execute(
            "INSERT OR IGNORE INTO session_issuers(session_id,issuer) SELECT id,? FROM sessions",
            (app.state.settings.issuer,),
        )
        db.execute("DELETE FROM session_issuers WHERE session_id NOT IN (SELECT id FROM sessions)")
    path.chmod(0o600)

    def delete_session(db, session_id):
        db.execute("DELETE FROM session_issuers WHERE session_id=?", (session_id,))
        db.execute("DELETE FROM sessions WHERE id=?", (session_id,))

    def origin(request):
        value = request.headers.get("Origin")
        if value not in ORIGINS:
            raise APIError(403, "INVALID_ORIGIN", "same-origin browser request required")
        return value

    def exchange(data, issuer):
        endpoint = _token_endpoint(app, issuer)
        try:
            with httpx.Client(verify=_oidc_ssl_context(), trust_env=False, timeout=20) as client:
                result = client.post(endpoint, data={"client_id": "desktop", **data})
            if result.status_code == 400:
                body = result.json()
                if data.get("grant_type") == "authorization_code":
                    if "Offline tokens not allowed" in body.get("error_description", ""):
                        raise APIError(403, "OFFLINE_ACCESS_DENIED", "账号尚未启用持续登录权限，请联系管理员")
                    raise APIError(401, "LOGIN_CODE_REJECTED", "登录授权码无效，请重新开始登录")
                if body.get("error") == "invalid_grant":
                    raise APIError(401, "SESSION_REVOKED", "login has been revoked; sign in again")
                raise APIError(503, "AUTH_UNAVAILABLE", "identity provider could not refresh the session")
            result.raise_for_status()
            body = result.json()
            claims = jwt.decode(
                body["access_token"],
                app.state.settings.public_key,
                algorithms=["RS256"],
                issuer=issuer,
                audience=app.state.settings.audience,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
            actor = app.state.authorization_resolver(body["access_token"])
            if "desktop:read" not in actor.scopes:
                raise APIError(401, "DESKTOP_ACCESS_REVOKED", "desktop permission revoked")
            return body, claims
        except (httpx.HTTPError, OSError, ValueError, KeyError, jwt.PyJWTError):
            raise APIError(503, "AUTH_UNAVAILABLE", "identity service unavailable") from None

    def output(token, expires, cookie=None):
        result = JSONResponse({"token": token, "expires": max(1, int(expires - time.time()))})
        result.headers["Cache-Control"] = "no-store"
        if cookie:
            result.set_cookie(
                COOKIE, cookie, max_age=630720000, secure=True, httponly=True, samesite="strict", path="/"
            )
        return result

    @app.post("/api/v1/auth/session/exchange")
    def create(body: Exchange, request: Request):
        request_origin = origin(request)
        if body.redirect != request_origin + "/":
            raise APIError(422, "INVALID_REDIRECT", "desktop callback required")
        issuer = _issuer_for_origin(app.state.settings, request_origin)
        tokens, claims = exchange(
            {
                "grant_type": "authorization_code",
                "code": body.code,
                "code_verifier": body.verifier,
                "redirect_uri": body.redirect,
            },
            issuer,
        )
        if not tokens.get("refresh_token"):
            raise APIError(503, "REFRESH_UNAVAILABLE", "persistent login unavailable")
        cookie = secrets.token_urlsafe(48)
        session_id = hashlib.sha256(cookie.encode()).hexdigest()
        with transaction(path) as db:
            old = request.cookies.get(COOKIE)
            if old:
                delete_session(db, hashlib.sha256(old.encode()).hexdigest())
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (session_id, claims["sub"], tokens["refresh_token"], tokens["access_token"], claims["exp"]),
            )
            db.execute("INSERT INTO session_issuers(session_id,issuer) VALUES(?,?)", (session_id, issuer))
        result = output(tokens["access_token"], claims["exp"])
        result.set_cookie(
            COOKIE, cookie, max_age=630720000, secure=True, httponly=True, samesite="strict", path="/"
        )
        return result

    @app.post("/api/v1/auth/session/token")
    def token(request: Request):
        origin(request)
        cookie = request.cookies.get(COOKIE, "")
        session_id = hashlib.sha256(cookie.encode()).hexdigest()
        # Serialize rotating refresh credentials across requests and workers.
        with transaction(path) as db:
            row = db.execute(
                """SELECT sessions.*, COALESCE(session_issuers.issuer, ?) AS issuer
                   FROM sessions LEFT JOIN session_issuers ON session_issuers.session_id=sessions.id
                   WHERE sessions.id=?""",
                (app.state.settings.issuer, session_id),
            ).fetchone()
            if not row:
                raise APIError(401, "NO_SESSION", "sign in required")
            if row["expires"] > time.time() + 60:
                try:
                    actor = app.state.authorization_resolver(row["access"])
                except APIError as exc:
                    if exc.status not in (401, 403):
                        raise
                    revoked = APIError(401, "DESKTOP_ACCESS_REVOKED", "desktop permission revoked")
                else:
                    revoked = (
                        APIError(401, "DESKTOP_ACCESS_REVOKED", "desktop permission revoked")
                        if "desktop:read" not in actor.scopes
                        else None
                )
                if revoked:
                    delete_session(db, session_id)
                else:
                    return output(row["access"], row["expires"], cookie)
            else:
                try:
                    session_issuer = row["issuer"] or app.state.settings.issuer
                    tokens, claims = exchange(
                        {"grant_type": "refresh_token", "refresh_token": row["refresh"]}, session_issuer
                    )
                except APIError as exc:
                    if exc.code != "SESSION_REVOKED":
                        raise
                    revoked = exc
                else:
                    revoked = (
                        APIError(401, "INVALID_SESSION", "identity changed")
                        if claims["sub"] != row["subject"]
                        else None
                    )
                    if not revoked:
                        db.execute(
                            "UPDATE sessions SET refresh=?,access=?,expires=? WHERE id=?",
                            (
                                tokens.get("refresh_token", row["refresh"]),
                                tokens["access_token"],
                                claims["exp"],
                                session_id,
                            ),
                        )
                        return output(tokens["access_token"], claims["exp"], cookie)
                if revoked:
                    delete_session(db, session_id)
        raise revoked

    @app.post("/api/v1/auth/session/logout")
    def logout(request: Request):
        origin(request)
        cookie = request.cookies.get(COOKIE, "")
        with transaction(path) as db:
            delete_session(db, hashlib.sha256(cookie.encode()).hexdigest())
        result = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        result.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
        return result
