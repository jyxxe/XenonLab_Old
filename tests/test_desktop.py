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
    assert client.get(url, headers=auth("bob")).json()["data"]["theme"] == "system"
    for invalid in ({"timezone": "Unknown/Zone"}, {"theme": None}, {"unexpected": True}):
        assert client.patch(url, headers=auth(), json=invalid).status_code == 422
    restarted = TestClient(create_app("desktop-web", settings))
    assert restarted.get(url, headers=auth()).json()["data"] == changed
