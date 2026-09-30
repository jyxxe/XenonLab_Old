import json
import os
from urllib.parse import urlparse

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, response, scope, transaction


class AppEntry(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    title: str = Field(min_length=1, max_length=80)
    origin: str
    entry: str = "/"
    icon: str = "◇"
    default_size: tuple[int, int] = (1000, 700)
    singleton: bool = True
    permissions: list[str] = Field(default_factory=list)


class Layout(BaseModel):
    dock: list[str] = Field(default_factory=list, max_length=30)
    windows: list[dict] = Field(default_factory=list, max_length=30)


def create_app(settings=None):
    app = base_app("desktop-web", settings)
    path = app.state.settings.data_dir / "desktop.sqlite"
    with transaction(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS apps(id TEXT PRIMARY KEY,definition TEXT);
        CREATE TABLE IF NOT EXISTS layouts(owner TEXT PRIMARY KEY,layout TEXT);
        """)

    def check_origin(body):
        if body.origin.rstrip("/") == os.getenv("DESKTOP_ORIGIN", "https://xenon.lan").rstrip("/"):
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

    @app.get("/api/v1/auth/config")
    def auth_config():
        return {"issuer": app.state.settings.issuer, "client_id": "desktop"}

    @app.get("/api/v1/apps")
    def apps(request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            entries = [json.loads(r[0]) for r in db.execute("SELECT definition FROM apps ORDER BY id")]
        return response(request, [a for a in entries if set(a["permissions"]) <= actor.scopes])

    @app.post("/api/v1/apps", status_code=201)
    def add(body: AppEntry, request: Request, actor=Depends(scope("desktop:admin"))):
        check_origin(body)
        with transaction(path) as db:
            if db.execute("SELECT 1 FROM apps WHERE id=?", (body.id,)).fetchone():
                raise APIError(409, "APP_EXISTS", "application already exists")
            db.execute("INSERT INTO apps VALUES(?,?)", (body.id, body.model_dump_json()))
        return response(request, body.model_dump())

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
