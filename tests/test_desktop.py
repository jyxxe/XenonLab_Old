import io
import json
import sqlite3

from fastapi.testclient import TestClient
from PIL import Image

from xeonolab.services import create_app

from .conftest import auth


def test_registry_and_layout_permissions(settings):
    settings.dev_tokens["admin"]["scope"] += " desktop:admin desktop:read"
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    entry = {"id": "library", "title": "XeLibrary", "origin": "https://library.xenon.lan"}
    assert client.post("/api/v1/apps", headers=auth(), json=entry).status_code == 403
    assert client.post("/api/v1/apps", headers=auth("admin"), json=entry).status_code == 201
    assert (
        client.post(
            "/api/v1/apps",
            headers=auth("admin"),
            json={**entry, "id": "bad", "origin": "javascript:alert(1)"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v1/apps", headers=auth("admin"), json={**entry, "id": "bad", "entry": "//attacker"}
        ).status_code
        == 422
    )
    assert len(client.get("/api/v1/apps", headers=auth()).json()["data"]) == 1
    assert (
        client.put("/api/v1/users/me/layout", headers=auth(), json={"dock": ["library"]}).status_code == 200
    )
    assert client.get("/api/v1/users/me/layout", headers=auth("admin")).json()["data"]["dock"] == []


def test_app_changes_are_versioned_and_audited(settings):
    settings.dev_tokens["admin"]["scope"] += " desktop:admin desktop:read"
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    reserved_dashboard = client.post(
        "/api/v1/apps",
        headers=auth("admin"),
        json={"id": "dashboard", "title": "Conflicting app", "origin": "https://dashboard.example"},
    )
    assert reserved_dashboard.status_code == 422
    assert reserved_dashboard.json()["error"]["code"] == "RESERVED_APP_ID"
    entry = {"id": "editor", "title": "Editor", "origin": "https://EDITOR.xenon.lan:443"}
    created = client.post("/api/v1/apps", headers=auth("admin"), json=entry)
    assert created.status_code == 201
    assert created.json()["data"]["origin"] == "https://editor.xenon.lan"
    assert created.json()["data"]["sandbox"]["allow_downloads"] is False
    resource = client.get("/api/v1/apps/editor", headers=auth("admin"))
    headers = auth("admin", **{"If-Match": resource.headers["etag"]})
    assert client.patch("/api/v1/apps/editor", headers=auth(), json={"title": "Changed"}).status_code == 403
    assert (
        client.patch(
            "/api/v1/apps/editor", headers=headers, json={"permissions": ["desktop:admin"]}
        ).status_code
        == 200
    )
    assert client.delete("/api/v1/apps/editor", headers=headers).status_code == 412
    assert client.get("/api/v1/apps/editor", headers=auth()).status_code == 404
    assert client.get("/api/v1/apps", headers=auth()).json()["data"] == []
    assert client.get("/api/v1/apps?include_unavailable=true", headers=auth()).status_code == 403
    resource = client.get("/api/v1/apps/editor", headers=auth("admin"))
    assert (
        client.delete(
            "/api/v1/apps/editor", headers=auth("admin", **{"If-Match": resource.headers["etag"]})
        ).status_code
        == 200
    )
    events = client.get("/api/v1/users/me/app-audit", headers=auth("admin")).json()["data"]
    assert [event["event"] for event in events] == ["app.deleted", "app.updated", "app.created"]


def test_preferences_merge_isolate_and_survive_restart(settings):
    for token in ("alice", "bob"):
        settings.dev_tokens[token]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/preferences"
    assert (
        client.patch(url, headers=auth(), json={"theme": "dark", "timezone": "Asia/Shanghai"}).status_code
        == 200
    )
    changed = client.patch(url, headers=auth(), json={"accent": "purple"}).json()["data"]
    assert changed["theme"] == "dark" and changed["timezone"] == "Asia/Shanghai"
    assert changed["widgets"] == ["clock", "status"]
    changed = client.patch(url, headers=auth(), json={"widgets": ["clock"]}).json()["data"]
    assert changed["widgets"] == ["clock"]
    changed = client.patch(
        url,
        headers=auth(),
        json={"widgets": ["calendar", "app-shortcuts", "work", "notes", "search"], "shortcut_apps": ["knowledge", "settings"], "notes": "保存后跨设备同步"},
    ).json()["data"]
    assert changed["widgets"] == ["calendar", "app-shortcuts", "work", "notes", "search"]
    assert changed["shortcut_apps"] == ["knowledge", "settings"]
    assert changed["notes"] == "保存后跨设备同步"
    assert changed["dock_size"] == "medium" and changed["dock_autohide"] is False
    assert changed["restore_windows"] is True and changed["reduced_motion"] is False
    changed = client.patch(
        url, headers=auth(), json={"dock_size": "large", "dock_autohide": True, "reduced_motion": True}
    ).json()["data"]
    assert changed["dock_size"] == "large" and changed["dock_autohide"] is True and changed["reduced_motion"] is True
    assert client.get(url, headers=auth("bob")).json()["data"]["theme"] == "system"
    assert client.get(url, headers=auth("bob")).json()["data"]["notes"] == ""
    for invalid in (
        {"timezone": "Unknown/Zone"},
        {"theme": None},
        {"unexpected": True},
        {"widgets": ["bogus"]},
        {"widgets": "clock"},
        {"dock_size": "huge"},
        {"reduced_motion": "not-a-bool"},
        {"shortcut_apps": ["x"] * 13},
        {"wallpaper_color": "not-a-color"},
        {"wallpaper_gradient_angle": 361},
        {"wallpaper_dim": 91},
        {"notes": "x" * 4001},
        {"wallpaper_image": "data:image/webp;base64,AAAA"},
    ):
        assert client.patch(url, headers=auth(), json=invalid).status_code == 422
    restarted = TestClient(create_app("desktop-web", settings))
    assert restarted.get(url, headers=auth()).json()["data"] == changed


def test_preferences_etag_guards_concurrent_writes(settings):
    """Issue #72 review: multi-device writers must be able to detect lost
    updates instead of silently overwriting each other."""
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/preferences"
    fresh = client.get(url, headers=auth())
    etag = fresh.headers["etag"]
    assert etag.startswith('"revision:')
    stale_write = client.patch(url, headers=auth(), json={"accent": "blue"})
    assert stale_write.status_code == 200  # legacy clients without If-Match keep working
    current = client.get(url, headers=auth())
    assert current.headers["etag"] != etag
    lost = client.patch(url, headers=auth(**{"If-Match": etag}), json={"accent": "green"})
    assert lost.status_code == 412 and lost.json()["error"]["code"] == "STALE_REVISION"
    ok = client.patch(
        url, headers=auth(**{"If-Match": current.headers["etag"]}), json={"accent": "green"}
    )
    assert ok.status_code == 200 and ok.json()["data"]["accent"] == "green"


def test_preferences_favorite_tombstones_roundtrip(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/preferences"
    saved = client.patch(
        url,
        headers=auth(),
        json={
            "library_favorites": [{"kind": "table", "id": "t1", "title": "T", "at": 10}],
            "library_favorite_tombstones": [
                {"kind": "table", "id": "t2", "at": 20},
                {"kind": "table", "id": "bad", "deleted": "yes"},
                {"nonsense": True},
            ],
        },
    )
    assert saved.status_code == 200
    data = saved.json()["data"]
    assert data["library_favorites"][0]["id"] == "t1"
    # The cleaner normalises tombstones and forces the deletion marker.
    assert data["library_favorite_tombstones"] == [
        {"kind": "table", "id": "t2", "at": 20, "deleted": True},
        {"kind": "table", "id": "bad", "at": 0, "deleted": True},
    ]


def test_layout_remembers_explicitly_empty_dock(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/layout"
    saved = client.put(url, headers=auth(), json={"dock": [], "windows": [], "dock_initialized": True})
    assert saved.status_code == 200
    assert saved.json()["data"]["dock_initialized"] is True
    restarted = TestClient(create_app("desktop-web", settings))
    assert restarted.get(url, headers=auth()).json()["data"]["dock_initialized"] is True


def test_preferences_persist_library_recents_and_favorites(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/preferences"
    recents = [
        {"kind": "table", "id": "t1", "title": "表一", "at": 20},
        {"kind": "row", "id": "t1:row-2", "title": "行二", "at": 10},
        {"junk": True},
        {"kind": "", "id": "x", "title": "dropped", "at": 5},
    ]
    favorites = [{"kind": "table", "id": "t9", "title": "x" * 500, "at": 7}]
    patch = client.patch(url, headers=auth(), json={"library_recents": recents, "library_favorites": favorites})
    assert patch.status_code == 200, patch.text
    stored = patch.json()["data"]
    assert stored["library_recents"] == [
        {"kind": "table", "id": "t1", "title": "表一", "at": 20},
        {"kind": "row", "id": "t1:row-2", "title": "行二", "at": 10},
    ]
    assert stored["library_favorites"][0]["title"] == "x" * 200
    assert len(client.patch(url, headers=auth(), json={
        "library_favorites": [{"kind": "table", "id": f"t{i}", "title": "x", "at": i} for i in range(80)],
    }).json()["data"]["library_favorites"]) == 50
    restarted = TestClient(create_app("desktop-web", settings))
    assert restarted.get(url, headers=auth()).json()["data"]["library_recents"][0]["id"] == "t1"
    # another account stays isolated
    settings.dev_tokens["bob"]["scope"] += " desktop:read"
    assert restarted.get(url, headers=auth("bob")).json()["data"]["library_recents"] == []


def test_preferences_etag_guards_multi_device_writes(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/preferences"
    first = client.get(url, headers=auth())
    assert first.status_code == 200
    etag_before = first.headers["ETag"]
    assert etag_before == '"revision:0"'
    written = client.patch(
        url,
        headers=auth(),
        json={"library_recents": [{"kind": "table", "id": "t1", "title": "A", "at": 3}]},
    )
    assert written.status_code == 200
    etag_after = written.headers["ETag"]
    assert etag_after != etag_before, "the PATCH response carries the new content revision"
    assert client.get(url, headers=auth()).headers["ETag"] == etag_after

    # Device B still holds the stale content revision: its write must be rejected
    # instead of silently overwriting device A's newer state.
    stale = client.patch(
        url,
        headers=auth(**{"If-Match": etag_before}),
        json={"library_recents": [{"kind": "table", "id": "t2", "title": "B", "at": 4}]},
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "STALE_REVISION"

    # After refreshing, the same device can apply its merged state.
    refreshed = client.get(url, headers=auth()).headers["ETag"]
    applied = client.patch(
        url,
        headers=auth(**{"If-Match": refreshed}),
        json={"library_favorites": [{"kind": "table", "id": "t9", "at": 9}]},
    )
    assert applied.status_code == 200
    assert client.get(url, headers=auth()).json()["data"]["library_favorites"][0]["id"] == "t9"


def test_preferences_tombstones_suppress_stale_device_favorites(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/preferences"
    favorite = {"kind": "table", "id": "t1", "title": "A", "at": 1}
    assert client.patch(url, headers=auth(), json={"library_favorites": [favorite]}).status_code == 200

    # Device A deletes the favorite: an empty list plus a newer deletion marker.
    tombstone = {"kind": "table", "id": "t1", "title": "A", "at": 10, "deleted": True}
    deleted = client.patch(
        url,
        headers=auth(),
        json={"library_favorites": [], "library_favorite_tombstones": [tombstone]},
    )
    assert deleted.status_code == 200
    state = client.get(url, headers=auth()).json()["data"]
    assert state["library_favorites"] == []
    # The server normalises tombstones to kind/id/at/deleted (titles are not needed
    # to suppress a stale cached favourite).
    assert state["library_favorite_tombstones"] == [
        {"kind": "table", "id": "t1", "at": 10, "deleted": True}
    ]

    # A device that recorded a recent while offline uploads it on remount; a later
    # read from another device sees it, so a third device can converge.
    offline_recent = {"kind": "row", "id": "t1:row-1", "title": "离线记录", "at": 11}
    assert client.patch(url, headers=auth(), json={"library_recents": [offline_recent]}).status_code == 200
    assert client.get(url, headers=auth()).json()["data"]["library_recents"][0]["id"] == "t1:row-1"


def test_custom_wallpaper_is_validated_persistent_and_account_scoped(settings):
    for token in ("alice", "bob"):
        settings.dev_tokens[token]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    preferences = "/api/v1/users/me/preferences"
    wallpaper = "/api/v1/users/me/wallpaper"
    solid = client.patch(preferences, headers=auth(), json={"wallpaper": "custom", "wallpaper_mode": "solid"})
    assert solid.status_code == 200
    assert client.patch(preferences, headers=auth(), json={"wallpaper": "custom", "wallpaper_mode": "image"}).status_code == 409

    output = io.BytesIO()
    Image.new("RGB", (32, 18), (30, 90, 170)).save(output, format="PNG")
    uploaded = client.put(
        wallpaper,
        headers=auth(**{"Content-Type": "application/octet-stream"}),
        content=output.getvalue(),
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["data"]["wallpaper"] == "custom"
    assert uploaded.json()["data"]["media_type"] == "image/webp"
    assert client.patch(preferences, headers=auth(), json={"wallpaper": "custom", "wallpaper_mode": "image"}).status_code == 200
    image = client.get(wallpaper, headers=auth())
    assert image.status_code == 200 and image.headers["content-type"] == "image/webp"
    with Image.open(io.BytesIO(image.content)) as decoded:
        assert decoded.format == "WEBP" and decoded.size == (32, 18)
    assert client.get(wallpaper, headers=auth("bob")).status_code == 404

    restarted = TestClient(create_app("desktop-web", settings))
    assert restarted.get(wallpaper, headers=auth()).status_code == 200
    assert restarted.get(preferences, headers=auth()).json()["data"]["wallpaper"] == "custom"
    removed = restarted.delete(wallpaper, headers=auth())
    assert removed.status_code == 200 and removed.json()["data"]["wallpaper"] == "aurora"
    assert restarted.get(wallpaper, headers=auth()).status_code == 404
    assert restarted.get(preferences, headers=auth()).json()["data"]["wallpaper"] == "aurora"


def test_legacy_custom_wallpaper_preference_defaults_to_account_image_mode(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    wallpaper = "/api/v1/users/me/wallpaper"
    output = io.BytesIO()
    Image.new("RGB", (12, 9), (90, 30, 120)).save(output, format="PNG")
    assert client.put(wallpaper, headers=auth(), content=output.getvalue()).status_code == 200
    assert client.patch("/api/v1/users/me/preferences", headers=auth(), json={"wallpaper": "custom", "wallpaper_mode": "image"}).status_code == 200

    database = settings.data_dir / "desktop.sqlite"
    with sqlite3.connect(database) as db:
        definition = json.loads(db.execute("SELECT definition FROM preferences WHERE owner='alice'").fetchone()[0])
        definition.pop("wallpaper_mode")
        db.execute("UPDATE preferences SET definition=? WHERE owner='alice'", (json.dumps(definition),))
    loaded = client.get("/api/v1/users/me/preferences", headers=auth()).json()["data"]
    assert loaded["wallpaper"] == "custom" and loaded["wallpaper_mode"] == "image"
    patched = client.patch("/api/v1/users/me/preferences", headers=auth(), json={"accent": "purple"}).json()["data"]
    assert patched["wallpaper_mode"] == "image" and patched["wallpaper"] == "custom"


def test_custom_wallpaper_rejects_fake_content_and_oversize(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    wallpaper = "/api/v1/users/me/wallpaper"
    fake = client.put(wallpaper, headers=auth(), content=b"not an image")
    assert fake.status_code == 422
    large = client.put(wallpaper, headers=auth(), content=b"x" * (5 * 1024 * 1024 + 1))
    assert large.status_code == 413
    assert client.get(wallpaper, headers=auth("opencode")).status_code == 403
    assert client.get(wallpaper).status_code == 401


def test_wallpaper_openapi_describes_binary_image_response(settings):
    contract = create_app("desktop-web", settings).openapi()["paths"]["/api/v1/users/me/wallpaper"]
    assert contract["put"]["requestBody"]["content"]["application/octet-stream"]["schema"]["format"] == "binary"
    assert "image/webp" in contract["get"]["responses"]["200"]["content"]
