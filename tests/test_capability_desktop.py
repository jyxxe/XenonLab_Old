"""Unified desktop discovery preserves legacy descriptors and gates."""

from fastapi.testclient import TestClient

from xeonolab.capabilities import load_registry
from xeonolab.services import create_app

from .conftest import auth
from .test_plugins import desktop_settings, publish


def test_builtin_desktop_manifest_covers_existing_adapters():
    capabilities = load_registry().all()["capabilities"]
    apps = {item["id"].removeprefix("builtin.platform.app."): item for item in capabilities if item["kind"] == "app"}
    assert set(apps) == {
        "knowledge", "chat", "development", "issues", "embedding-queue", "agent-prompts",
        "system", "embedding", "settings", "harness",
    }
    assert apps["knowledge"]["permissions"] == ["xelibrary:read"]
    assert apps["issues"]["permissions"] == apps["embedding-queue"]["permissions"] == ["dev:read"]
    assert apps["embedding"]["permissions"] == ["desktop:read"]
    assert {item["id"].removeprefix("builtin.platform.widget.") for item in capabilities if item["kind"] == "widget"} == {
        "clock", "status", "calendar", "app-shortcuts", "work", "search", "notes",
    }


def test_unified_discovery_matches_legacy_descriptors_and_disable(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    entry = {"id": "example-console", "title": "Example", "origin": "https://console.example", "permissions": ["desktop:read"]}
    assert client.post("/api/v1/apps", headers=auth("admin"), json=entry).status_code == 201
    assert publish(client).status_code == 201
    assert client.post("/api/v1/plugins/hello-plugin/install", headers=auth()).status_code == 200
    apps = client.get("/api/v1/apps", headers=auth()).json()["data"]
    widgets = client.get("/api/v1/users/me/plugin-widgets", headers=auth()).json()["data"]
    capabilities = client.get("/api/v1/capabilities", headers=auth()).json()["data"]
    assert {(item["kind"], item["desktop_id"]) for item in capabilities if item["source"] != "builtin"} == {
        ("app", "example-console"), ("app", "hello-app"), ("widget", "hello-widget"),
    }
    assert {entry["id"] for entry in apps} == {"example-console", "hello-app"}
    assert widgets[0]["id"] == "hello-widget"
    assert "desktop_id" not in apps[0] and "desktop_id" not in widgets[0]
    assert client.patch("/api/v1/plugins/hello-plugin/install", headers=auth(), json={"enabled": False}).status_code == 200
    remaining = client.get("/api/v1/capabilities", headers=auth()).json()["data"]
    assert not any(item["source"] == "plugin" for item in remaining)
    assert any(item["source"] == "registered" for item in remaining)


def test_registered_apps_require_permissions_and_track_deletion(settings):
    client = TestClient(create_app("desktop-web", desktop_settings(settings)))
    entry = {"id": "restricted-console", "title": "Restricted", "origin": "https://console.example", "permissions": ["desktop:admin"]}
    assert client.post("/api/v1/apps", headers=auth("admin"), json=entry).status_code == 201
    assert not any(item["source"] == "registered" for item in client.get("/api/v1/capabilities", headers=auth()).json()["data"])
    assert any(item["source"] == "registered" for item in client.get("/api/v1/capabilities", headers=auth("admin")).json()["data"])
    resource = client.get("/api/v1/apps/restricted-console", headers=auth("admin"))
    assert client.delete("/api/v1/apps/restricted-console", headers={**auth("admin"), "If-Match": resource.headers["etag"]}).status_code == 200
    assert not any(item["source"] == "registered" for item in client.get("/api/v1/capabilities", headers=auth("admin")).json()["data"])
