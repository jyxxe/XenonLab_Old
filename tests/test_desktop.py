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
