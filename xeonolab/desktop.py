import json
import os
import re
import time
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import APIError, base_app, connect, etag, precondition, response, scope, transaction


class Sandbox(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_scripts: bool = True
    allow_forms: bool = True
    allow_downloads: bool = False


class AppEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=80)
    origin: str = Field(max_length=2048)
    entry: str = Field(default="/", max_length=2048)
    icon: str = Field(default="◇", max_length=64)
    default_size: tuple[int, int] = (1000, 700)
    singleton: bool = True
    permissions: list[str] = Field(default_factory=list, max_length=64)
    sandbox: Sandbox = Field(default_factory=Sandbox)


class AppPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str | None = None
    origin: str | None = None
    entry: str | None = None
    icon: str | None = None
    default_size: tuple[int, int] | None = None
    singleton: bool | None = None
    permissions: list[str] | None = None
    sandbox: Sandbox | None = None


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: Literal["system", "light", "dark"] = "system"
    accent: Literal["blue", "purple", "green", "orange"] = "blue"
    wallpaper: Literal["aurora", "ocean", "sunset", "silver"] = "aurora"
    timezone: str = Field(default="system", min_length=1, max_length=80)
    widgets: list[Literal["clock", "status"]] = Field(
        default_factory=lambda: ["clock", "status"], max_length=8
    )

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value != "system":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("IANA timezone required") from None
        return value


class PreferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: str | None = None
    accent: str | None = None
    wallpaper: str | None = None
    timezone: str | None = None
    widgets: list[str] | None = None


class Layout(BaseModel):
    dock: list[str] = Field(default_factory=list, max_length=30)
    windows: list[dict] = Field(default_factory=list, max_length=30)


def create_app(settings=None):
    app = base_app("desktop-web", settings)
    from .accounts import install as install_accounts
    from .browser_sessions import install

    install_accounts(app)
    install(app)
    path = app.state.settings.data_dir / "desktop.sqlite"
    with transaction(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS apps(id TEXT PRIMARY KEY,definition TEXT);
        CREATE TABLE IF NOT EXISTS layouts(owner TEXT PRIMARY KEY,layout TEXT);
        CREATE TABLE IF NOT EXISTS preferences(owner TEXT PRIMARY KEY,definition TEXT);
        CREATE TABLE IF NOT EXISTS app_audit(id TEXT PRIMARY KEY,actor TEXT,event TEXT,target TEXT,at REAL);
        """)

        if "revision" not in {row[1] for row in db.execute("PRAGMA table_info(apps)")}:
            db.execute("ALTER TABLE apps ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")

    def canonical_origin(value):
        parsed = urlparse(value)
        try:
            host = (parsed.hostname or "").encode("idna").decode().lower()
            if host:
                try:
                    ip_address(host)
                except ValueError:
                    if len(host) > 253 or not all(
                        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                        for label in host.split(".")
                    ):
                        raise ValueError("invalid hostname")
            return (
                parsed.scheme.lower(),
                host,
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
            )
        except (ValueError, UnicodeError):
            raise APIError(422, "INVALID_ORIGIN", "invalid origin hostname or port") from None

    def check_origin(body):
        if body.id in {"knowledge", "settings", "work"}:
            raise APIError(422, "RESERVED_APP_ID", "built-in application ID is reserved")
        desktops = {
            "https://xenon.lan",
            "https://xenon.jyxxe.com",
            "https://192.168.0.101:9443",
            os.getenv("DESKTOP_ORIGIN", "https://xenon.lan"),
        }
        if canonical_origin(body.origin) in {canonical_origin(value) for value in desktops}:
            raise APIError(422, "SEPARATE_ORIGIN_REQUIRED", "iframe apps need their own origin")
        parsed = urlparse(body.origin)
        if (
            parsed.scheme not in ("https", "http")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise APIError(422, "INVALID_ORIGIN", "absolute HTTP origin required")
        if app.state.settings.mode == "production" and parsed.scheme != "https":
            raise APIError(422, "HTTPS_REQUIRED", "production app origins require HTTPS")
        if (
            not body.entry.startswith("/")
            or body.entry.startswith("//")
            or "\\" in body.entry
            or any(ord(c) < 32 for c in body.entry)
        ):
            raise APIError(422, "INVALID_ENTRY", "relative application entry required")
        if not all(200 <= dimension <= 4000 for dimension in body.default_size):
            raise APIError(422, "INVALID_SIZE", "application size outside bounds")
        scheme, host, port = canonical_origin(body.origin)
        host = "[" + host + "]" if ":" in host else host
        body.origin = (
            scheme + "://" + host + (":" + str(port) if port != (443 if scheme == "https" else 80) else "")
        )

    def app_row(db, aid):
        row = db.execute("SELECT definition,revision FROM apps WHERE id=?", (aid,)).fetchone()
        if not row:
            raise APIError(404, "APP_NOT_FOUND", "application not found")
        return {**json.loads(row["definition"]), "revision": row["revision"]}

    def app_audit(db, actor, event, aid):
        db.execute(
            "INSERT INTO app_audit VALUES(?,?,?,?,?)", (str(uuid4()), actor.sub, event, aid, time.time())
        )

    @app.get("/api/v1/auth/config")
    def auth_config():
        return {"issuer": app.state.settings.issuer, "client_id": "desktop"}

    @app.get("/api/v1/users/me")
    def me(request: Request, actor=Depends(scope("desktop:read"))):
        return response(request, {"subject": actor.sub, "kind": actor.kind, "scopes": sorted(actor.scopes)})

    @app.get("/api/v1/apps")
    def apps(request: Request, include_unavailable: bool = False, actor=Depends(scope("desktop:read"))):
        if include_unavailable:
            actor.require("desktop:admin")
        with connect(path) as db:
            entries = [
                {**json.loads(row["definition"]), "revision": row["revision"]}
                for row in db.execute("SELECT definition,revision FROM apps ORDER BY id")
            ]
        return response(
            request,
            [entry for entry in entries if include_unavailable or set(entry["permissions"]) <= actor.scopes],
        )

    @app.get("/api/v1/apps/{aid}")
    def get_app(aid: str, request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            entry = app_row(db, aid)
        if "desktop:admin" not in actor.scopes and not set(entry["permissions"]) <= actor.scopes:
            raise APIError(404, "APP_NOT_FOUND", "application not found")
        from fastapi.responses import JSONResponse

        return JSONResponse(response(request, entry), headers={"ETag": etag(entry["revision"])})

    @app.post("/api/v1/apps", status_code=201)
    def add(body: AppEntry, request: Request, actor=Depends(scope("desktop:admin"))):
        check_origin(body)
        with transaction(path) as db:
            if db.execute("SELECT 1 FROM apps WHERE id=?", (body.id,)).fetchone():
                raise APIError(409, "APP_EXISTS", "application already exists")
            db.execute("INSERT INTO apps(id,definition) VALUES(?,?)", (body.id, body.model_dump_json()))
            app_audit(db, actor, "app.created", body.id)
        return response(request, {**body.model_dump(), "revision": 1})

    @app.patch("/api/v1/apps/{aid}")
    def update_app(aid: str, body: AppPatch, request: Request, actor=Depends(scope("desktop:admin"))):
        with transaction(path) as db:
            previous = app_row(db, aid)
            precondition(request, previous.pop("revision"))
            try:
                updated = AppEntry.model_validate({**previous, **body.model_dump(exclude_unset=True)})
            except ValueError:
                raise APIError(422, "INVALID_APP", "invalid application fields") from None
            check_origin(updated)
            db.execute(
                "UPDATE apps SET definition=?,revision=revision+1 WHERE id=?",
                (updated.model_dump_json(), aid),
            )
            app_audit(db, actor, "app.updated", aid)
            return response(request, app_row(db, aid))

    @app.delete("/api/v1/apps/{aid}")
    def delete_app(aid: str, request: Request, actor=Depends(scope("desktop:admin"))):
        with transaction(path) as db:
            entry = app_row(db, aid)
            precondition(request, entry["revision"])
            db.execute("DELETE FROM apps WHERE id=?", (aid,))
            app_audit(db, actor, "app.deleted", aid)
        return response(request, {"deleted": True})

    @app.get("/api/v1/users/me/preferences")
    def get_preferences(request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            row = db.execute("SELECT definition FROM preferences WHERE owner=?", (actor.sub,)).fetchone()
        return response(
            request,
            Preferences.model_validate_json(row[0]).model_dump() if row else Preferences().model_dump(),
        )

    @app.patch("/api/v1/users/me/preferences")
    def patch_preferences(body: PreferencePatch, request: Request, actor=Depends(scope("desktop:read"))):
        with transaction(path) as db:
            row = db.execute("SELECT definition FROM preferences WHERE owner=?", (actor.sub,)).fetchone()
            old = json.loads(row[0]) if row else Preferences().model_dump()
            try:
                updated = Preferences.model_validate({**old, **body.model_dump(exclude_unset=True)})
            except ValueError:
                raise APIError(422, "INVALID_PREFERENCES", "invalid preference or timezone") from None
            db.execute(
                "INSERT OR REPLACE INTO preferences VALUES(?,?)", (actor.sub, updated.model_dump_json())
            )
        return response(request, updated.model_dump())

    @app.get("/api/v1/users/me/app-audit")
    def registry_audit(request: Request, actor=Depends(scope("desktop:admin"))):
        with connect(path) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM app_audit ORDER BY at DESC LIMIT 200")]
        return response(request, rows)

    @app.get("/api/v1/users/me/layout")
    def get_layout(request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            row = db.execute("SELECT layout FROM layouts WHERE owner=?", (actor.sub,)).fetchone()
        return response(request, json.loads(row[0]) if row else {"dock": [], "windows": []})

    @app.put("/api/v1/users/me/layout")
    def save(body: Layout, request: Request, actor=Depends(scope("desktop:read"))):
        with transaction(path) as db:
            db.execute("INSERT OR REPLACE INTO layouts VALUES(?,?)", (actor.sub, body.model_dump_json()))
        return response(request, body.model_dump())

    return app
