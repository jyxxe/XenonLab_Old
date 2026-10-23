import asyncio
import io
import json
import os
import re
import threading
import time
import warnings
from ipaddress import ip_address
from typing import Literal
from urllib.parse import urlparse
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import anyio
from fastapi import Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .agent_prompts import load_admin_prompts
from .capabilities import install as install_capabilities
from .common import APIError, base_app, connect, etag, precondition, response, scope, transaction
from .wallpaper_backup import wallpaper_data_lock
from .wallpaper_limits import WALLPAPER_MAX_BYTES, WALLPAPER_MAX_PIXELS


class _BoundedImageBuffer(io.BytesIO):
    def write(self, data):
        if self.tell() + len(data) > WALLPAPER_MAX_BYTES:
            raise APIError(
                413, "WALLPAPER_TOO_LARGE_AFTER_ENCODING", "encoded wallpaper exceeds the 5 MiB limit"
            )
        return super().write(data)


def process_wallpaper_image(payload: bytes, temporary):
    """Decode and encode an untrusted image. Always called in a bounded worker thread."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as source:
                if source.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("unsupported image format")
                if source.width * source.height > WALLPAPER_MAX_PIXELS or getattr(source, "n_frames", 1) != 1:
                    raise ValueError("image dimensions or frame count outside limits")
                source.verify()
            with Image.open(io.BytesIO(payload)) as source:
                image = ImageOps.exif_transpose(source)
                image = image.convert("RGBA" if "A" in image.getbands() else "RGB")
                with _BoundedImageBuffer() as output:
                    image.save(output, format="WEBP", quality=90, method=4)
                    size = output.tell()
                    temporary.write_bytes(output.getvalue())
        return size
    except APIError:
        temporary.unlink(missing_ok=True)
        raise
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        temporary.unlink(missing_ok=True)
        raise APIError(422, "INVALID_WALLPAPER", "file is not a supported, safe image") from None
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def publish_wallpaper(database, wallpaper_root, data_dir, owner, asset_id, temporary, size):
    target = wallpaper_root / (str(UUID(asset_id)) + ".webp")
    with wallpaper_data_lock(data_dir):
        previous_id = None
        try:
            with transaction(database) as db:
                previous = db.execute(
                    "SELECT asset_id FROM desktop_wallpapers WHERE owner=?", (owner,)
                ).fetchone()
                previous_id = previous[0] if previous else None
                os.replace(temporary, target)
                db.execute(
                    "INSERT OR REPLACE INTO desktop_wallpapers VALUES(?,?,?,?,?)",
                    (owner, asset_id, "image/webp", size, time.time()),
                )
        except Exception:
            temporary.unlink(missing_ok=True)
            target.unlink(missing_ok=True)
            raise
        if previous_id:
            wallpaper_root.joinpath(str(UUID(previous_id)) + ".webp").unlink(missing_ok=True)


async def _wait_for_wallpaper_worker(worker):
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        # Starlette/AnyIO request task groups use cancellation scopes, which can
        # re-cancel every await in an ordinary except block. Shield the wait so
        # the processing slot and temporary file remain owned until the thread exits.
        with anyio.CancelScope(shield=True):
            await worker
        raise


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
    wallpaper: Literal["aurora", "ocean", "sunset", "silver", "custom"] = "aurora"
    wallpaper_mode: Literal["solid", "gradient", "image"] = "solid"
    wallpaper_color: str = Field(default="#101828", pattern=r"^#[0-9a-fA-F]{6}$")
    wallpaper_gradient_from: str = Field(default="#0b1220", pattern=r"^#[0-9a-fA-F]{6}$")
    wallpaper_gradient_to: str = Field(default="#1d4ed8", pattern=r"^#[0-9a-fA-F]{6}$")
    wallpaper_gradient_angle: int = Field(default=160, ge=0, le=360)
    wallpaper_dim: int = Field(default=0, ge=0, le=90)
    timezone: str = Field(default="system", min_length=1, max_length=80)
    widgets: list[Literal["clock", "status", "calendar", "app-shortcuts", "work", "notes", "search"]] = Field(
        default_factory=lambda: ["clock", "status"], max_length=8
    )
    plugin_widgets: list[str] = Field(default_factory=list, max_length=8)
    shortcut_apps: list[str] = Field(default_factory=list, max_length=12)
    notes: str = Field(default="", max_length=4000)
    dock_size: Literal["small", "medium", "large"] = "medium"
    dock_autohide: bool = False
    restore_windows: bool = True
    animations: bool = True
    reduced_motion: bool = False
    library_recents: list[dict] = Field(default_factory=list, max_length=50)
    library_favorites: list[dict] = Field(default_factory=list, max_length=50)
    library_favorite_tombstones: list[dict] = Field(default_factory=list, max_length=200)

    @field_validator("library_recents", "library_favorites")
    @classmethod
    def _clean_library_entries_fields(cls, value):
        return _clean_library_entries(value)

    @field_validator("library_favorite_tombstones")
    @classmethod
    def _clean_library_tombstones(cls, value):
        return _clean_library_tombstones(value)

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value):
        if value != "system":
            try:
                ZoneInfo(value)
            except (ZoneInfoNotFoundError, ValueError):
                raise ValueError("IANA timezone required") from None
        return value

    @field_validator("plugin_widgets")
    @classmethod
    def valid_plugin_widgets(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("duplicate plugin widget")
        if not all(re.fullmatch(r"[a-z][a-z0-9-]{1,63}", value) for value in values):
            raise ValueError("invalid plugin widget id")
        return values


def _clean_library_entries(value):
    """Normalize server-persisted library recents/favorites entries (issue #72)."""
    cleaned = []
    for entry in value or []:
        if not isinstance(entry, dict):
            continue
        kind, entry_id = entry.get("kind"), entry.get("id")
        if not isinstance(kind, str) or not kind or not isinstance(entry_id, str) or not entry_id:
            continue
        at = entry.get("at")
        cleaned.append({
            "kind": kind[:32],
            "id": entry_id[:128],
            "title": str(entry.get("title", ""))[:200],
            "at": at if isinstance(at, (int, float)) and not isinstance(at, bool) else 0,
        })
    return cleaned[:50]


def _clean_library_tombstones(value):
    """Deletion markers for cross-device favorites sync (issue #72 review):
    a tombstone with a newer `at` than an entry suppresses its revival."""
    cleaned = []
    for entry in value or []:
        if not isinstance(entry, dict):
            continue
        kind, entry_id = entry.get("kind"), entry.get("id")
        if not isinstance(kind, str) or not kind or not isinstance(entry_id, str) or not entry_id:
            continue
        at = entry.get("at")
        cleaned.append({
            "kind": kind[:32],
            "id": entry_id[:128],
            "at": at if isinstance(at, (int, float)) and not isinstance(at, bool) else 0,
            "deleted": True,
        })
    return cleaned[:200]


class PreferencePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    theme: str | None = None
    accent: str | None = None
    wallpaper: str | None = None
    wallpaper_mode: str | None = None
    wallpaper_color: str | None = None
    wallpaper_gradient_from: str | None = None
    wallpaper_gradient_to: str | None = None
    wallpaper_gradient_angle: int | None = None
    wallpaper_dim: int | None = None
    timezone: str | None = None
    widgets: list[str] | None = None
    plugin_widgets: list[str] | None = Field(default=None, max_length=8)
    shortcut_apps: list[str] | None = Field(default=None, max_length=12)
    notes: str | None = Field(default=None, max_length=4000)
    dock_size: str | None = None
    dock_autohide: bool | None = None
    restore_windows: bool | None = None
    animations: bool | None = None
    reduced_motion: bool | None = None
    library_recents: list[dict] | None = None
    library_favorites: list[dict] | None = None
    library_favorite_tombstones: list[dict] | None = None

    @field_validator("library_recents", "library_favorites")
    @classmethod
    def _clean_patch_library_entries(cls, value):
        return None if value is None else _clean_library_entries(value)

    @field_validator("library_favorite_tombstones")
    @classmethod
    def _clean_patch_library_tombstones(cls, value):
        return None if value is None else _clean_library_tombstones(value)


def preferences_from_definition(definition: str | None) -> Preferences:
    """Load old account rows without changing the meaning of uploaded custom wallpapers."""
    if definition is None:
        return Preferences()
    value = json.loads(definition)
    if value.get("wallpaper") == "custom" and "wallpaper_mode" not in value:
        # The previous schema used `custom` exclusively for the account image.
        value["wallpaper_mode"] = "image"
    return Preferences.model_validate(value)


class Layout(BaseModel):
    dock_initialized: bool = False
    dock: list[str] = Field(default_factory=list, max_length=30)
    windows: list[dict] = Field(default_factory=list, max_length=30)


def create_app(settings=None):
    app = base_app("desktop-web", settings)
    from .accounts import install as install_accounts
    from .browser_sessions import install

    install_accounts(app)
    install(app)
    path = app.state.settings.data_dir / "desktop.sqlite"
    wallpaper_root = app.state.settings.data_dir / "wallpapers"
    wallpaper_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    wallpaper_root.chmod(0o700)
    app.state.wallpaper_processing_slots = threading.BoundedSemaphore(2)
    with transaction(path) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS apps(id TEXT PRIMARY KEY,definition TEXT);
        CREATE TABLE IF NOT EXISTS layouts(owner TEXT PRIMARY KEY,layout TEXT);
        CREATE TABLE IF NOT EXISTS preferences(owner TEXT PRIMARY KEY,definition TEXT);
        CREATE TABLE IF NOT EXISTS app_audit(id TEXT PRIMARY KEY,actor TEXT,event TEXT,target TEXT,at REAL);
        CREATE TABLE IF NOT EXISTS desktop_wallpapers(owner TEXT PRIMARY KEY,asset_id TEXT NOT NULL,
          media_type TEXT NOT NULL,size INTEGER NOT NULL,updated_at REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS plugins(id TEXT PRIMARY KEY,version TEXT NOT NULL,
          manifest TEXT NOT NULL,app_id TEXT,published_by TEXT NOT NULL,published_at REAL NOT NULL,
          updated_at REAL NOT NULL,revision INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE IF NOT EXISTS plugin_installs(owner TEXT NOT NULL,
          plugin_id TEXT NOT NULL REFERENCES plugins(id) ON DELETE CASCADE,
          enabled INTEGER NOT NULL DEFAULT 1,installed_version TEXT NOT NULL,
          manifest TEXT NOT NULL,catalog_revision INTEGER NOT NULL,installed_at REAL NOT NULL,
          PRIMARY KEY(owner,plugin_id));
        CREATE TABLE IF NOT EXISTS plugin_audit(id TEXT PRIMARY KEY,actor TEXT NOT NULL,
          event TEXT NOT NULL,plugin_id TEXT NOT NULL,at REAL NOT NULL);
        """)

        if "revision" not in {row[1] for row in db.execute("PRAGMA table_info(apps)")}:
            db.execute("ALTER TABLE apps ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "revision" not in {row[1] for row in db.execute("PRAGMA table_info(preferences)")}:
            db.execute("ALTER TABLE preferences ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
        plugin_columns = {row[1] for row in db.execute("PRAGMA table_info(plugins)")}
        if "app_id" not in plugin_columns:
            # Additive repair for a database created by the first draft of the
            # plugin slice. Deployed databases do not contain these tables yet.
            db.execute("ALTER TABLE plugins ADD COLUMN app_id TEXT")
            db.execute("UPDATE plugins SET app_id=json_extract(manifest,'$.app.id')")
        if "revision" not in plugin_columns:
            db.execute("ALTER TABLE plugins ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS plugins_app_id ON plugins(app_id) WHERE app_id IS NOT NULL"
        )
        install_columns = {row[1] for row in db.execute("PRAGMA table_info(plugin_installs)")}
        if "manifest" not in install_columns or "catalog_revision" not in install_columns:
            if "manifest" not in install_columns:
                db.execute("ALTER TABLE plugin_installs ADD COLUMN manifest TEXT NOT NULL DEFAULT '{}'")
            if "catalog_revision" not in install_columns:
                db.execute(
                    "ALTER TABLE plugin_installs ADD COLUMN catalog_revision INTEGER NOT NULL DEFAULT 1"
                )
            db.execute(
                "UPDATE plugin_installs SET manifest=COALESCE("
                "(SELECT manifest FROM plugins p WHERE p.id=plugin_id),'{}'), "
                "catalog_revision=COALESCE((SELECT revision FROM plugins p WHERE p.id=plugin_id),1)"
            )
            # A first-draft row whose plugin no longer exists cannot be projected;
            # `{}` is not a valid manifest, so drop only those orphaned snapshots.
            db.execute("DELETE FROM plugin_installs WHERE manifest='{}' OR manifest IS NULL")

    def canonical_origin(value):
        parsed = urlparse(value)
        try:
            host = (parsed.hostname or "").encode("idna").decode().lower()
            if host:
                try:
                    ip_address(host)
                except ValueError:
                    labels = host.split(".")
                    if len(host) > 253 or not all(
                        re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                        for label in labels
                    ):
                        raise ValueError("invalid hostname")
                    # Browsers normalize legacy IPv4 spellings (0177.0.0.1,
                    # 127.1, 0x7f.1) to the same address, while Python keeps them
                    # as names. Reject numeric hosts that are not canonical IPv4,
                    # or a plugin origin could pass the separate-origin check
                    # while the browser treats it as the desktop origin.
                    if all(re.fullmatch(r"(?:0x[0-9a-f]+|\d+)", label) for label in labels):
                        raise ValueError("invalid hostname")
            return (
                parsed.scheme.lower(),
                host,
                parsed.port or (443 if parsed.scheme.lower() == "https" else 80),
            )
        except (ValueError, UnicodeError):
            raise APIError(422, "INVALID_ORIGIN", "invalid origin hostname or port") from None

    def check_origin(body):
        if body.id in {"dashboard", "knowledge", "settings", "work"}:
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

    from .plugins import install_plugins

    install_plugins(app, path, check_origin)

    @app.get("/api/v1/auth/config")
    def auth_config(origin: str = Query(default="", max_length=256)):
        from .browser_sessions import _issuer_for_origin

        # The SPA supplies its own exact origin. This lets the public hostname and
        # direct LAN/IP entry use separately configured, strictly verified issuers.
        browser_origin = origin
        issuer = (
            app.state.settings.issuer
            if not browser_origin and not app.state.settings.oidc_issuers_by_origin
            else _issuer_for_origin(app.state.settings, browser_origin)
        )
        return {"issuer": issuer, "client_id": "desktop"}

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
            if not include_unavailable:
                # Plugin apps are per-account, read-only projections of the plugin
                # catalog. They are never written into the administrator-managed
                # `apps` table, so the admin registry cannot edit or delete them.
                from .plugins import installed_apps

                known = {entry["id"] for entry in entries}
                entries.extend(
                    entry for entry in installed_apps(db, actor.sub, actor.scopes) if entry["id"] not in known
                )
        entries.sort(key=lambda entry: entry["id"])
        return response(
            request,
            [entry for entry in entries if include_unavailable or set(entry["permissions"]) <= actor.scopes],
        )

    @app.get("/api/v1/apps/{aid}")
    def get_app(aid: str, request: Request, actor=Depends(scope("desktop:read"))):
        with connect(path) as db:
            try:
                entry = app_row(db, aid)
            except APIError as error:
                if error.code != "APP_NOT_FOUND":
                    raise
                from .plugins import installed_apps

                entry = next(
                    (item for item in installed_apps(db, actor.sub, actor.scopes) if item["id"] == aid),
                    None,
                )
                if entry is None:
                    raise
        if "desktop:admin" not in actor.scopes and not set(entry["permissions"]) <= actor.scopes:
            raise APIError(404, "APP_NOT_FOUND", "application not found")
        return JSONResponse(response(request, entry), headers={"ETag": etag(entry["revision"])})

    @app.post("/api/v1/apps", status_code=201)
    def add(body: AppEntry, request: Request, actor=Depends(scope("desktop:admin"))):
        check_origin(body)
        with transaction(path) as db:
            if db.execute("SELECT 1 FROM apps WHERE id=?", (body.id,)).fetchone():
                raise APIError(409, "APP_EXISTS", "application already exists")
            if db.execute("SELECT 1 FROM plugins WHERE app_id=?", (body.id,)).fetchone():
                raise APIError(409, "APP_EXISTS", "application id is already provided by a plugin")
            from .plugins import installed_app_ids

            if installed_app_ids(db).get(body.id):
                raise APIError(409, "APP_EXISTS", "application id is still installed by a plugin")
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
            row = db.execute("SELECT definition,revision FROM preferences WHERE owner=?", (actor.sub,)).fetchone()
            has_wallpaper = db.execute(
                "SELECT 1 FROM desktop_wallpapers WHERE owner=?", (actor.sub,)
            ).fetchone() is not None
        result = preferences_from_definition(row["definition"] if row else None).model_dump()
        result["custom_wallpaper_available"] = has_wallpaper
        # Content-revision ETag so multi-device library pref writers can detect
        # lost updates (issue #72 review); legacy clients that never send
        # If-Match keep the old last-write-wins behaviour.
        return JSONResponse(response(request, result), headers={"ETag": etag(row["revision"] if row else 0)})

    @app.patch("/api/v1/users/me/preferences")
    def patch_preferences(body: PreferencePatch, request: Request, actor=Depends(scope("desktop:read"))):
        with transaction(path) as db:
            row = db.execute("SELECT definition,revision FROM preferences WHERE owner=?", (actor.sub,)).fetchone()
            old = preferences_from_definition(row["definition"] if row else None).model_dump()
            current_revision = row["revision"] if row else 0
            if request.headers.get("If-Match"):
                precondition(request, current_revision)
            try:
                updated = Preferences.model_validate({**old, **body.model_dump(exclude_unset=True)})
            except ValueError:
                raise APIError(422, "INVALID_PREFERENCES", "invalid preference or timezone") from None
            if updated.wallpaper == "custom" and updated.wallpaper_mode == "image" and not db.execute(
                "SELECT 1 FROM desktop_wallpapers WHERE owner=?", (actor.sub,)
            ).fetchone():
                raise APIError(409, "WALLPAPER_NOT_FOUND", "upload a custom wallpaper before selecting it")
            db.execute(
                "INSERT OR REPLACE INTO preferences(owner,definition,revision) VALUES(?,?,?)",
                (actor.sub, updated.model_dump_json(), current_revision + 1),
            )
            has_wallpaper = db.execute(
                "SELECT 1 FROM desktop_wallpapers WHERE owner=?", (actor.sub,)
            ).fetchone() is not None
        # Returning the new ETag lets clients continue writing without a
        # lost-update round trip (issue #72 review).
        return JSONResponse(
            response(request, {**updated.model_dump(), "custom_wallpaper_available": has_wallpaper}),
            headers={"ETag": etag(current_revision + 1)},
        )

    def wallpaper_file(asset_id):
        # Only UUIDs generated by this service are ever used to form a path.
        return wallpaper_root / (str(UUID(asset_id)) + ".webp")

    @app.put(
        "/api/v1/users/me/wallpaper",
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
        }}},
    )
    async def upload_wallpaper(request: Request, actor=Depends(scope("desktop:read"))):
        slots = app.state.wallpaper_processing_slots
        if not slots.acquire(blocking=False):
            raise APIError(429, "WALLPAPER_PROCESSING_BUSY", "wallpaper image processing is at capacity")
        temporary = None
        try:
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > WALLPAPER_MAX_BYTES:
                    raise APIError(413, "WALLPAPER_TOO_LARGE", "wallpaper exceeds the 5 MiB limit")
                raw.extend(chunk)
            if not raw:
                raise APIError(422, "INVALID_WALLPAPER", "choose a non-empty JPEG, PNG, or WebP image")
            asset_id = str(uuid4())
            temporary = wallpaper_root / (asset_id + ".tmp")
            worker = asyncio.create_task(asyncio.to_thread(process_wallpaper_image, bytes(raw), temporary))
            size = await _wait_for_wallpaper_worker(worker)
            worker = asyncio.create_task(asyncio.to_thread(
                publish_wallpaper, path, wallpaper_root, app.state.settings.data_dir,
                actor.sub, asset_id, temporary, size
            ))
            await _wait_for_wallpaper_worker(worker)
            return response(request, {"wallpaper": "custom", "media_type": "image/webp", "size": size})
        finally:
            slots.release()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    @app.get(
        "/api/v1/users/me/wallpaper",
        response_class=Response,
        responses={200: {"content": {"image/webp": {
            "schema": {"type": "string", "format": "binary"}
        }}}},
    )
    def get_wallpaper(request: Request, actor=Depends(scope("desktop:read"))):
        with wallpaper_data_lock(app.state.settings.data_dir), connect(path) as db:
            row = db.execute(
                "SELECT asset_id,media_type,size FROM desktop_wallpapers WHERE owner=?", (actor.sub,)
            ).fetchone()
            if row is None:
                raise APIError(404, "WALLPAPER_NOT_FOUND", "no custom wallpaper is saved for this account")
            target = wallpaper_file(row["asset_id"])
            if not target.is_file():
                raise APIError(404, "WALLPAPER_NOT_FOUND", "saved wallpaper file is unavailable")
            content = target.read_bytes()
        return Response(content, media_type=row["media_type"], headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"
        })

    @app.delete("/api/v1/users/me/wallpaper")
    def delete_wallpaper(request: Request, actor=Depends(scope("desktop:read"))):
        with wallpaper_data_lock(app.state.settings.data_dir):
            with transaction(path) as db:
                row = db.execute(
                    "SELECT asset_id FROM desktop_wallpapers WHERE owner=?", (actor.sub,)
                ).fetchone()
                db.execute("DELETE FROM desktop_wallpapers WHERE owner=?", (actor.sub,))
                stored = db.execute(
                    "SELECT definition,revision FROM preferences WHERE owner=?", (actor.sub,)
                ).fetchone()
                old = preferences_from_definition(stored["definition"] if stored else None).model_dump()
                if old.get("wallpaper") == "custom" and old.get("wallpaper_mode", "image") == "image":
                    old["wallpaper"] = "aurora"
                db.execute(
                    "INSERT OR REPLACE INTO preferences(owner,definition,revision) VALUES(?,?,?)",
                    (actor.sub, json.dumps(old), (stored["revision"] if stored else 0) + 1),
                )
            if row:
                wallpaper_file(row["asset_id"]).unlink(missing_ok=True)
        return response(request, {"deleted": True, "wallpaper": old["wallpaper"]})

    @app.get("/api/v1/agent-prompts")
    def admin_agent_prompts(request: Request, actor=Depends(scope("desktop:admin"))):
        """Admin-only credential-bearing prompt catalogue (files live outside Git)."""
        return response(request, load_admin_prompts())

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

    install_capabilities(app, path)

    return app
