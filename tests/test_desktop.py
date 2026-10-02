from fastapi.testclient import TestClient

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
    assert changed["wallpaper"] == "aurora" and changed["wallpaper_mode"] == "solid"
    assert changed["wallpaper_image"] == "" and changed["wallpaper_dim"] == 0
    assert changed["notes"] == ""
    changed = client.patch(url, headers=auth(), json={"widgets": ["clock"]}).json()["data"]
    assert changed["widgets"] == ["clock"]
    assert changed["dock_size"] == "medium" and changed["dock_autohide"] is False
    assert changed["restore_windows"] is True and changed["reduced_motion"] is False
    changed = client.patch(
        url, headers=auth(), json={"dock_size": "large", "dock_autohide": True, "reduced_motion": True}
    ).json()["data"]
    assert changed["dock_size"] == "large" and changed["dock_autohide"] is True and changed["reduced_motion"] is True
    assert client.get(url, headers=auth("bob")).json()["data"]["theme"] == "system"
    for invalid in (
        {"timezone": "Unknown/Zone"},
        {"theme": None},
        {"unexpected": True},
        {"widgets": ["bogus"]},
        {"widgets": "clock"},
        {"dock_size": "huge"},
        {"reduced_motion": "not-a-bool"},
        {"wallpaper": "rainbow"},
        {"wallpaper_mode": "animated"},
        {"wallpaper_color": "blue"},
        {"wallpaper_gradient_angle": 361},
        {"wallpaper_dim": 91},
        {"wallpaper_image": "https://cdn.example/wallpaper.png"},
        {"wallpaper_image": "data:image/svg+xml;base64,PHN2Zy8+"},
        {"notes": "x" * 4001},
    ):
        assert client.patch(url, headers=auth(), json=invalid).status_code == 422
    restarted = TestClient(create_app("desktop-web", settings))
    assert restarted.get(url, headers=auth()).json()["data"] == changed


def test_preferences_custom_wallpaper_widgets_and_notes(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/preferences"
    png_data_url = "data:image/png;base64," + "A" * 64
    saved = client.patch(
        url,
        headers=auth(),
        json={
            "wallpaper": "custom",
            "wallpaper_mode": "image",
            "wallpaper_image": png_data_url,
            "wallpaper_dim": 30,
            "widgets": ["clock", "calendar", "work", "search", "notes"],
            "notes": "回路测试用便签",
        },
    )
    assert saved.status_code == 200
    data = saved.json()["data"]
    assert data["wallpaper"] == "custom" and data["wallpaper_mode"] == "image"
    assert data["wallpaper_image"] == png_data_url and data["wallpaper_dim"] == 30
    assert data["widgets"] == ["clock", "calendar", "work", "search", "notes"]
    assert data["notes"] == "回路测试用便签"
    # An oversized image is rejected instead of bloating the preferences row.
    oversized = client.patch(
        url, headers=auth(), json={"wallpaper_image": "data:image/png;base64," + "A" * 2_800_001}
    )
    assert oversized.status_code == 422
    # The stored row still holds the previous, valid wallpaper after the rejection.
    assert client.get(url, headers=auth()).json()["data"]["wallpaper_image"] == png_data_url


def test_layout_remembers_explicitly_empty_dock(settings):
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    url = "/api/v1/users/me/layout"
    saved = client.put(url, headers=auth(), json={"dock": [], "windows": [], "dock_initialized": True})
    assert saved.status_code == 200
    assert saved.json()["data"]["dock_initialized"] is True
    restarted = TestClient(create_app("desktop-web", settings))
    assert restarted.get(url, headers=auth()).json()["data"]["dock_initialized"] is True
