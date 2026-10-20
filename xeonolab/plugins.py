"""Declarative plugin catalog and per-account installation state.

Plugins are untrusted declarations, not executable server code. The service never
imports, evaluates or fetches a plugin; it stores a manifest and lets the desktop
open the plugin's own HTTPS origin in a sandboxed iframe. This keeps the platform
API boundary intact and means a compromised plugin origin cannot run code in the
desktop or acquire the user's bearer token.

The catalog is the server-side ("cloud") copy of plugin metadata. Binary/package
hosting is intentionally out of scope for this slice: a manifest only points at
an independently hosted origin. See docs/adr/0008-plugin-catalog.md.
"""

import json
import re
import time
from types import SimpleNamespace
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import APIError, connect, etag, precondition, response, scope, transaction

PLUGIN_ID = r"^[a-z][a-z0-9-]{1,63}$"
APP_ID = r"^[a-z][a-z0-9-]{0,63}$"
WIDGET_ID = r"^[a-z][a-z0-9-]{0,63}$"
SCOPE = r"^[a-z][a-z0-9:.-]{1,79}$"
VERSION = r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]{1,64})?$"

# Built-in native applications and the configurable chat slot cannot be replaced
# by a plugin. The desktop's app registry also rejects a smaller legacy set.
RESERVED_APP_IDS = {
    "chat",
    "dashboard",
    "development",
    "embedding",
    "issues",
    "knowledge",
    "library",
    "openwebui",
    "settings",
    "system",
    "work",
}


class PluginSandbox(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_scripts: bool = True
    allow_forms: bool = True
    allow_downloads: bool = False


class PluginApp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=APP_ID)
    title: str = Field(min_length=1, max_length=80)
    origin: str = Field(max_length=2048)
    entry: str = Field(default="/", max_length=2048)
    icon: str = Field(default="◇", max_length=64)
    default_size: tuple[int, int] = (1000, 700)
    singleton: bool = True
    sandbox: PluginSandbox = Field(default_factory=PluginSandbox)


class PluginWidget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=WIDGET_ID)
    title: str = Field(min_length=1, max_length=80)
    origin: str = Field(max_length=2048)
    entry: str = Field(default="/", max_length=2048)
    icon: str = Field(default="◇", max_length=64)
    height: int = Field(default=180, ge=80, le=480)


class PluginManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=PLUGIN_ID)
    name: str = Field(min_length=1, max_length=80)
    version: str = Field(pattern=VERSION)
    description: str = Field(default="", max_length=600)
    author: str = Field(default="", max_length=120)
    homepage: str = Field(default="", max_length=2048)
    icon: str = Field(default="◈", max_length=64)
    permissions: list[str] = Field(default_factory=list, max_length=32)
    app: PluginApp | None = None
    widget: PluginWidget | None = None

    @field_validator("permissions")
    @classmethod
    def valid_permissions(cls, values):
        unique = list(dict.fromkeys(values))
        if len(unique) != len(values):
            raise ValueError("permissions must be unique")
        if not all(re.fullmatch(SCOPE, value) for value in unique):
            raise ValueError("invalid permission scope")
        return unique

    @field_validator("homepage")
    @classmethod
    def valid_homepage(cls, value):
        if not value:
            return value
        parsed = urlparse(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("homepage must be an absolute HTTPS URL without credentials, query or fragment")
        return value

    @model_validator(mode="after")
    def has_capability(self):
        if self.app is None and self.widget is None:
            raise ValueError("plugin must declare an app or a widget")
        if self.app is not None and self.app.id in RESERVED_APP_IDS:
            raise ValueError("reserved app id")
        return self


class InstallPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


def _manifest(row):
    return PluginManifest.model_validate(json.loads(row["manifest"]))


def _installed_manifest(install):
    """The manifest snapshot accepted by this account at install/update time.

    Catalogue edits must not silently change an installed app or widget. Only an
    explicit reinstall (which re-checks permissions) advances the snapshot.
    """
    return PluginManifest.model_validate_json(install["manifest"])


def _install_row(db, owner, plugin_id):
    return db.execute(
        "SELECT * FROM plugin_installs WHERE owner=? AND plugin_id=?", (owner, plugin_id)
    ).fetchone()


def _catalog_item(db, row, owner, scopes):
    manifest = _manifest(row)
    install = _install_row(db, owner, manifest.id)
    missing = [value for value in manifest.permissions if value not in scopes]
    return {
        **manifest.model_dump(),
        "revision": row["revision"],
        "published_at": row["published_at"],
        "updated_at": row["updated_at"],
        "installed": install is not None,
        "enabled": bool(install["enabled"]) if install is not None else False,
        "installed_version": install["installed_version"] if install is not None else None,
        "installed_at": install["installed_at"] if install is not None else None,
        "missing_scopes": missing,
        "update_available": bool(install is not None and install["installed_version"] != manifest.version),
    }


def installed_apps(db, owner, scopes):
    """App entries for this account's enabled plugins, filtered by live scopes.

    The caller merges these with the administrator-managed registry. A plugin app
    id is unique across both stores by catalogue validation, so it cannot shadow a
    global app. An account that lost a requested scope stops seeing the app.
    """
    rows = db.execute(
        "SELECT * FROM plugin_installs WHERE owner=? AND enabled=1 ORDER BY plugin_id",
        (owner,),
    ).fetchall()
    entries = []
    for row in rows:
        manifest = _installed_manifest(row)
        if manifest.app is None or not set(manifest.permissions) <= scopes:
            continue
        entries.append(
            {
                **manifest.app.model_dump(),
                "permissions": manifest.permissions,
                "revision": row["catalog_revision"],
                "plugin_id": manifest.id,
                "plugin_version": manifest.version,
                "plugin_name": manifest.name,
                "description": manifest.description or f"插件 · {manifest.name}",
            }
        )
    return entries


def installed_widgets(db, owner, scopes):
    """Enabled widget plugins whose requested scopes the account still holds."""
    rows = db.execute(
        "SELECT * FROM plugin_installs WHERE owner=? AND enabled=1 ORDER BY plugin_id",
        (owner,),
    ).fetchall()
    widgets = []
    for row in rows:
        manifest = _installed_manifest(row)
        if manifest.widget is None or not set(manifest.permissions) <= scopes:
            continue
        widgets.append(
            {
                **manifest.widget.model_dump(),
                "plugin_id": manifest.id,
                "plugin_name": manifest.name,
                "plugin_version": manifest.version,
                "permissions": manifest.permissions,
            }
        )
    return widgets


def remove_widget_preference(db, owner, plugin_id):
    row = db.execute("SELECT definition FROM preferences WHERE owner=?", (owner,)).fetchone()
    if row is None:
        return
    try:
        value = json.loads(row["definition"])
    except (TypeError, json.JSONDecodeError):
        return
    selected = value.get("plugin_widgets")
    if not isinstance(selected, list) or plugin_id not in selected:
        return
    value["plugin_widgets"] = [item for item in selected if item != plugin_id]
    db.execute("UPDATE preferences SET definition=? WHERE owner=?", (json.dumps(value), owner))


def install_plugins(app, path, check_origin):
    """Register plugin routes on the desktop service.

    ``check_origin`` is the desktop's existing validator, reused so plugin
    origins obey exactly the same HTTPS, entry, size and origin-separation rules
    as administrator-registered iframe apps.
    """

    def audit(db, actor, event, plugin_id):
        db.execute(
            "INSERT INTO plugin_audit VALUES(?,?,?,?,?)",
            (str(uuid4()), actor.sub, event, plugin_id, time.time()),
        )

    def manifest_or_404(db, plugin_id):
        row = db.execute("SELECT * FROM plugins WHERE id=?", (plugin_id,)).fetchone()
        if row is None:
            raise APIError(404, "PLUGIN_NOT_FOUND", "plugin not found")
        return row

    def validate_manifest(manifest):
        if manifest.app is not None:
            check_origin(manifest.app)
        if manifest.widget is not None:
            # Reuse the desktop validator without exposing its app-only size field
            # in the plugin manifest.
            target = SimpleNamespace(
                id="plugin-widget", origin=manifest.widget.origin, entry=manifest.widget.entry,
                default_size=(320, 220),
            )
            check_origin(target)
            manifest.widget.origin = target.origin
        if manifest.app is not None and manifest.app.id in RESERVED_APP_IDS:
            raise APIError(422, "RESERVED_APP_ID", "plugin app id is reserved")

    def check_app_conflict(db, manifest, own_id):
        if manifest.app is None:
            return
        app_id = manifest.app.id
        if db.execute("SELECT 1 FROM apps WHERE id=?", (app_id,)).fetchone():
            raise APIError(409, "APP_ID_CONFLICT", "plugin app id conflicts with an existing application")
        clash = db.execute(
            "SELECT id FROM plugins WHERE app_id=? AND id<>?", (app_id, own_id)
        ).fetchone()
        if clash:
            raise APIError(409, "APP_ID_CONFLICT", "plugin app id conflicts with another plugin")

    def check_install_scope(manifest, actor):
        missing = [value for value in manifest.permissions if value not in actor.scopes]
        if missing:
            raise APIError(
                403,
                "PLUGIN_PERMISSION_DENIED",
                "plugin requires permissions this account does not hold",
                details={"missing_scopes": missing},
            )

    @app.get("/api/v1/plugins")
    def list_plugins(request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM plugins ORDER BY id")]
            items = [_catalog_item(db, row, actor.sub, actor.scopes) for row in rows]
        return response(request, items)

    @app.get("/api/v1/plugins/{plugin_id}")
    def get_plugin(plugin_id: str, request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            row = manifest_or_404(db, plugin_id)
            item = _catalog_item(db, row, actor.sub, actor.scopes)
        from fastapi.responses import JSONResponse

        return JSONResponse(response(request, item), headers={"ETag": etag(item["revision"])})

    @app.post("/api/v1/plugins", status_code=201)
    def publish(body: PluginManifest, request: Request, actor=Depends(scope("desktop:admin"))):
        validate_manifest(body)
        with transaction(path) as db:
            if db.execute("SELECT 1 FROM plugins WHERE id=?", (body.id,)).fetchone():
                raise APIError(409, "PLUGIN_EXISTS", "plugin already exists")
            check_app_conflict(db, body, body.id)
            now = time.time()
            db.execute(
                "INSERT INTO plugins(id,version,manifest,app_id,published_by,published_at,updated_at,revision) "
                "VALUES(?,?,?,?,?,?,?,1)",
                (
                    body.id,
                    body.version,
                    body.model_dump_json(),
                    body.app.id if body.app else None,
                    actor.sub,
                    now,
                    now,
                ),
            )
            audit(db, actor, "plugin.published", body.id)
            item = _catalog_item(db, manifest_or_404(db, body.id), actor.sub, actor.scopes)
        return response(request, item)

    @app.put("/api/v1/plugins/{plugin_id}")
    def update_plugin(
        plugin_id: str, body: PluginManifest, request: Request, actor=Depends(scope("desktop:admin"))
    ):
        if body.id != plugin_id:
            raise APIError(422, "PLUGIN_ID_MISMATCH", "path and manifest ids differ")
        validate_manifest(body)
        with transaction(path) as db:
            current = manifest_or_404(db, plugin_id)
            precondition(request, current["revision"])
            check_app_conflict(db, body, plugin_id)
            db.execute(
                "UPDATE plugins SET version=?,manifest=?,app_id=?,updated_at=?,revision=revision+1 WHERE id=?",
                (
                    body.version,
                    body.model_dump_json(),
                    body.app.id if body.app else None,
                    time.time(),
                    plugin_id,
                ),
            )
            audit(db, actor, "plugin.updated", plugin_id)
            item = _catalog_item(db, manifest_or_404(db, plugin_id), actor.sub, actor.scopes)
        return response(request, item)

    @app.delete("/api/v1/plugins/{plugin_id}")
    def delete_plugin(plugin_id: str, request: Request, actor=Depends(scope("desktop:admin"))):
        with transaction(path) as db:
            current = manifest_or_404(db, plugin_id)
            precondition(request, current["revision"])
            for owner in db.execute(
                "SELECT owner FROM plugin_installs WHERE plugin_id=?", (plugin_id,)
            ).fetchall():
                remove_widget_preference(db, owner["owner"], plugin_id)
            db.execute("DELETE FROM plugins WHERE id=?", (plugin_id,))
            audit(db, actor, "plugin.deleted", plugin_id)
        return response(request, {"deleted": True, "plugin_id": plugin_id})

    @app.post("/api/v1/plugins/{plugin_id}/install")
    def install(plugin_id: str, request: Request, actor=Depends(scope("desktop:read"))):
        with transaction(path) as db:
            row = manifest_or_404(db, plugin_id)
            manifest = _manifest(row)
            check_install_scope(manifest, actor)
            existing = _install_row(db, actor.sub, plugin_id)
            now = time.time()
            if existing is None:
                db.execute(
                    "INSERT INTO plugin_installs(owner,plugin_id,enabled,installed_version,"
                    "manifest,catalog_revision,installed_at) VALUES(?,?,1,?,?,?,?)",
                    (actor.sub, plugin_id, manifest.version, manifest.model_dump_json(), row["revision"], now),
                )
                audit(db, actor, "plugin.installed", plugin_id)
            else:
                db.execute(
                    "UPDATE plugin_installs SET enabled=1,installed_version=?,manifest=?,catalog_revision=? "
                    "WHERE owner=? AND plugin_id=?",
                    (
                        manifest.version,
                        manifest.model_dump_json(),
                        row["revision"],
                        actor.sub,
                        plugin_id,
                    ),
                )
                audit(db, actor, "plugin.updated_install", plugin_id)
            item = _catalog_item(db, row, actor.sub, actor.scopes)
        return response(request, item)

    @app.patch("/api/v1/plugins/{plugin_id}/install")
    def update_install(
        plugin_id: str, body: InstallPatch, request: Request, actor=Depends(scope("desktop:read"))
    ):
        with transaction(path) as db:
            row = manifest_or_404(db, plugin_id)
            install = _install_row(db, actor.sub, plugin_id)
            if install is None:
                raise APIError(404, "PLUGIN_NOT_INSTALLED", "plugin is not installed for this account")
            if body.enabled:
                # Enabling applies the snapshot the account actually installed, not
                # an unreviewed catalogue update.
                check_install_scope(_installed_manifest(install), actor)
            db.execute(
                "UPDATE plugin_installs SET enabled=? WHERE owner=? AND plugin_id=?",
                (1 if body.enabled else 0, actor.sub, plugin_id),
            )
            audit(db, actor, "plugin.enabled" if body.enabled else "plugin.disabled", plugin_id)
            item = _catalog_item(db, row, actor.sub, actor.scopes)
        return response(request, item)

    @app.delete("/api/v1/plugins/{plugin_id}/install")
    def uninstall(plugin_id: str, request: Request, actor=Depends(scope("desktop:read"))):
        with transaction(path) as db:
            manifest_or_404(db, plugin_id)
            removed = db.execute(
                "DELETE FROM plugin_installs WHERE owner=? AND plugin_id=?", (actor.sub, plugin_id)
            ).rowcount
            remove_widget_preference(db, actor.sub, plugin_id)
            if removed:
                audit(db, actor, "plugin.uninstalled", plugin_id)
        return response(request, {"deleted": bool(removed), "plugin_id": plugin_id})

    @app.get("/api/v1/users/me/plugin-widgets")
    def my_widgets(request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            return response(request, installed_widgets(db, actor.sub, actor.scopes))
