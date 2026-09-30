"""Persistent browser sessions; refresh credentials never leave the server."""

import hashlib
import os
import secrets
import ssl
import time

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
    redirect: str


def install(app):
    path = app.state.settings.data_dir / "browser-sessions.sqlite"
    with transaction(path) as db:
        db.execute("""CREATE TABLE IF NOT EXISTS sessions(
            id TEXT PRIMARY KEY, subject TEXT NOT NULL, refresh TEXT NOT NULL,
            access TEXT NOT NULL, expires REAL NOT NULL)""")
    path.chmod(0o600)

    def origin(request):
        value = request.headers.get("Origin")
        if value not in ORIGINS:
            raise APIError(403, "INVALID_ORIGIN", "same-origin browser request required")
        return value

    def exchange(data):
        endpoint = os.getenv("OIDC_TOKEN_URL")
        if not endpoint:
            raise APIError(503, "AUTH_NOT_CONFIGURED", "token endpoint unavailable")
        try:
            context = ssl.create_default_context(cafile=os.getenv("OIDC_CA_FILE"))
            with httpx.Client(verify=context, trust_env=False, timeout=20) as client:
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
                issuer=app.state.settings.issuer,
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
        if body.redirect != origin(request) + "/":
            raise APIError(422, "INVALID_REDIRECT", "desktop callback required")
        tokens, claims = exchange(
            {
                "grant_type": "authorization_code",
                "code": body.code,
                "code_verifier": body.verifier,
                "redirect_uri": body.redirect,
            }
        )
        if not tokens.get("refresh_token"):
            raise APIError(503, "REFRESH_UNAVAILABLE", "persistent login unavailable")
        cookie = secrets.token_urlsafe(48)
        session_id = hashlib.sha256(cookie.encode()).hexdigest()
        with transaction(path) as db:
            old = request.cookies.get(COOKIE)
            if old:
                db.execute("DELETE FROM sessions WHERE id=?", (hashlib.sha256(old.encode()).hexdigest(),))
            db.execute(
                "INSERT INTO sessions VALUES(?,?,?,?,?)",
                (
                    session_id,
                    claims["sub"],
                    tokens["refresh_token"],
                    tokens["access_token"],
                    claims["exp"],
                ),
            )
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
            row = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
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
                    db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
                else:
                    return output(row["access"], row["expires"], cookie)
            else:
                try:
                    tokens, claims = exchange({"grant_type": "refresh_token", "refresh_token": row["refresh"]})
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
                    db.execute("DELETE FROM sessions WHERE id=?", (session_id,))
        raise revoked

    @app.post("/api/v1/auth/session/logout")
    def logout(request: Request):
        origin(request)
        cookie = request.cookies.get(COOKIE, "")
        with transaction(path) as db:
            db.execute("DELETE FROM sessions WHERE id=?", (hashlib.sha256(cookie.encode()).hexdigest(),))
        result = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        result.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
        return result
