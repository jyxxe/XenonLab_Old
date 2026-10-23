"""Capability registry MVP (Epic #305 Phase 1): manifest validation and API."""

import json

import pytest
from fastapi.testclient import TestClient

from xeonolab.capabilities import CapabilityRegistry, load_registry
from xeonolab.common import APIError
from xeonolab.services import create_app

from .conftest import auth


def manifest(**overrides):
    base = {
        "id": "builtin.test",
        "version": "1.0.0",
        "api_version": "1",
        "source": "builtin",
        "capabilities": [
            {"id": "builtin.test.app.one", "kind": "app", "version": "1.0.0", "runtime": "ui", "permissions": ["desktop:read"], "dependencies": []},
        ],
    }
    base.update(overrides)
    return base


def test_builtin_manifest_is_valid_and_registry_lookup_works():
    registry = load_registry()
    data = registry.all()
    assert data["api_version"] == "1" and data["source"] == "builtin"
    assert len(data["capabilities"]) >= 10
    first = data["capabilities"][0]
    assert registry.lookup(first["id"])["id"] == first["id"]
    assert registry.lookup(first["id"], version="9.9.9") is None
    assert registry.lookup("builtin.test.missing") is None
    assert all(item["kind"] in {"app", "widget", "tool", "agent", "provider", "worker"} for item in data["capabilities"])


def test_registry_rejects_unsafe_or_inconsistent_manifests():
    for bad in (
        manifest(api_version="2"),
        manifest(version="1.0"),
        manifest(id="../escape"),
        manifest(capabilities=[]),
        manifest(capabilities=[{"id": "no-namespace", "kind": "app", "version": "1.0.0", "runtime": "ui", "permissions": [], "dependencies": []}]),
        manifest(capabilities=[{"id": "builtin.test.a", "kind": "unknown", "version": "1.0.0", "runtime": "ui", "permissions": [], "dependencies": []}]),
        manifest(capabilities=[{"id": "builtin.test.a", "kind": "app", "version": "1.0.0", "runtime": "root", "permissions": [], "dependencies": []}]),
    ):
        with pytest.raises(ValueError):
            CapabilityRegistry(bad)
    duplicate = manifest(capabilities=[
        {"id": "builtin.test.a", "kind": "app", "version": "1.0.0", "runtime": "ui", "permissions": [], "dependencies": []},
        {"id": "builtin.test.a", "kind": "widget", "version": "1.0.0", "runtime": "ui", "permissions": [], "dependencies": []},
    ])
    with pytest.raises(ValueError):
        CapabilityRegistry(duplicate)


def test_registry_rejects_dependency_cycles_and_bad_permissions():
    cycle = manifest(capabilities=[
        {"id": "builtin.test.a", "kind": "app", "version": "1.0.0", "runtime": "ui", "permissions": [], "dependencies": ["builtin.test.b@^1.0.0"]},
        {"id": "builtin.test.b", "kind": "provider", "version": "1.0.0", "runtime": "api", "permissions": [], "dependencies": ["builtin.test.a@^1.0.0"]},
    ])
    with pytest.raises(ValueError):
        CapabilityRegistry(cycle)
    bad_permission = manifest(capabilities=[
        {"id": "builtin.test.a", "kind": "app", "version": "1.0.0", "runtime": "ui", "permissions": ["ADMIN"], "dependencies": []},
    ])
    with pytest.raises(ValueError):
        CapabilityRegistry(bad_permission)


def test_missing_manifest_fails_closed(tmp_path):
    with pytest.raises(APIError) as error:
        load_registry(tmp_path / "missing.json")
    assert error.value.code == "CAPABILITY_REGISTRY_UNAVAILABLE"
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    with pytest.raises(APIError) as error:
        load_registry(broken)
    assert error.value.code == "CAPABILITY_REGISTRY_UNAVAILABLE"


def test_capabilities_endpoint_requires_desktop_scope_and_fails_closed(tmp_path, monkeypatch):
    import xeonolab.capabilities as capabilities_module

    settings = __import__("xeonolab.common", fromlist=["Settings"]).Settings(
        tmp_path, "development",
        dev_tokens={
            "alice": {"sub": "alice", "scope": "desktop:read", "agent_kind": "human"},
            "carol": {"sub": "carol", "scope": "xelibrary:read", "agent_kind": "human"},
        },
    )
    client = TestClient(create_app("desktop-web", settings))
    assert client.get("/api/v1/capabilities").status_code == 401
    assert client.get("/api/v1/capabilities", headers=auth("carol")).status_code == 403
    ok = client.get("/api/v1/capabilities", headers=auth("alice"))
    assert ok.status_code == 200 and ok.json()["data"] and ok.json()["meta"]["registry"]["manifest"]["id"]
    assert client.get("/api/v1/capabilities/builtin.platform.app.knowledge", headers=auth("alice")).status_code == 404
    assert client.get("/api/v1/capabilities/builtin.platform.app.system", headers=auth("alice")).status_code == 200
    assert client.get("/api/v1/capabilities/builtin.platform.missing", headers=auth("alice")).status_code == 404

    disk = tmp_path / "bad.json"
    disk.write_text(json.dumps(manifest(api_version="9")))
    monkeypatch.setattr(capabilities_module, "load_registry", lambda: load_registry(disk))
    broken = client.get("/api/v1/capabilities", headers=auth("alice"))
    assert broken.status_code == 503
    assert broken.json()["error"]["code"] == "CAPABILITY_REGISTRY_INVALID"


def test_runtime_capabilities_include_only_enabled_plugins(tmp_path):
    import json as jsonlib
    import sqlite3

    from xeonolab.capabilities import runtime_capabilities

    database = tmp_path / "desktop.sqlite"
    with sqlite3.connect(database) as db:
        db.execute(
            "CREATE TABLE plugin_installs(owner TEXT, plugin_id TEXT, enabled INTEGER, "
            "manifest TEXT, installed_version TEXT, catalog_revision INTEGER, installed_at REAL)"
        )
        manifest = {
            "version": "1.2.0",
            "permissions": ["desktop:read"],
            "app": {"id": "plugin-app", "title": "Plugin app"},
            "widget": {"id": "plugin-widget", "title": "Plugin widget"},
        }
        db.execute(
            "INSERT INTO plugin_installs VALUES(?,?,?,?,?,?,?)",
            ("alice", "acme.tools", 1, jsonlib.dumps(manifest), "1.2.0", 3, 0.0),
        )
        db.execute(
            "INSERT INTO plugin_installs VALUES(?,?,?,?,?,?,?)",
            ("alice", "acme.disabled", 0, jsonlib.dumps(manifest), "1.2.0", 3, 0.0),
        )
        db.execute(
            "INSERT INTO plugin_installs VALUES(?,?,?,?,?,?,?)",
            ("alice", "acme.denied", 1, jsonlib.dumps({**manifest, "permissions": ["desktop:admin"]}), "1.2.0", 3, 0.0),
        )
        db.execute(
            "INSERT INTO plugin_installs VALUES(?,?,?,?,?,?,?)",
            ("alice", "acme.broken", 1, "{not json", "1.2.0", 3, 0.0),
        )

    registry = load_registry()
    capabilities = runtime_capabilities(registry, database, "alice", ["desktop:read"])
    plugin_ids = {item["id"] for item in capabilities if item["source"] == "plugin"}
    assert plugin_ids == {"plugin.acme.tools.app", "plugin.acme.tools.widget"}
    assert all(item["enabled"] for item in capabilities if item["source"] == "plugin")
    assert {item["kind"] for item in capabilities if item["id"].startswith("plugin.acme.tools")} == {"app", "widget"}

    other = runtime_capabilities(registry, database, "bob", ["desktop:read"])
    assert not any(item["source"] == "plugin" for item in other)


def test_capability_version_constraints_and_incompatibility():
    from xeonolab.capabilities import CapabilityIncompatible, satisfies
    from xeonolab.common import APIError

    assert satisfies("1.2.3", "1.2.3")
    assert satisfies("1.2.4", "^1.2.3")
    assert not satisfies("2.0.0", "^1.2.3")
    assert satisfies("1.2.9", "~1.2.3")
    assert not satisfies("1.3.0", "~1.2.3")
    assert satisfies("0.0.1", "*")
    for bad in ("1.2", "x.y.z", "1.2.3.4"):
        try:
            satisfies("1.2.3", bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    assert issubclass(CapabilityIncompatible, APIError)


def test_version_requirement_is_enforced_by_the_endpoint(tmp_path, monkeypatch):

    settings = __import__("xeonolab.common", fromlist=["Settings"]).Settings(
        tmp_path, "development",
        dev_tokens={"alice": {"sub": "alice", "scope": "desktop:read", "agent_kind": "human"}},
    )
    client = TestClient(create_app("desktop-web", settings))
    target = "builtin.platform.app.system"
    ok = client.get(f"/api/v1/capabilities/{target}", params={"require": "^1.0.0"}, headers=auth("alice"))
    assert ok.status_code == 200 and ok.json()["meta"]["compatible"] is True
    bad = client.get(f"/api/v1/capabilities/{target}", params={"require": "^2.0.0"}, headers=auth("alice"))
    assert bad.status_code == 409 and bad.json()["error"]["code"] == "CAPABILITY_INCOMPATIBLE"
    invalid = client.get(f"/api/v1/capabilities/{target}", params={"require": "nope"}, headers=auth("alice"))
    assert invalid.status_code == 422


@pytest.mark.parametrize("kind", ["app", "widget"])
def test_plugin_resolution_rechecks_account_enablement_and_permissions(settings, kind):
    from .test_plugins import desktop_settings, publish

    settings = desktop_settings(settings)
    client = TestClient(create_app("desktop-web", settings))
    assert publish(client).status_code == 201
    install = "/api/v1/plugins/hello-plugin/install"
    target = f"/api/v1/capabilities/plugin.hello-plugin.{kind}"
    assert client.get(target, headers=auth()).status_code == 404
    assert client.post(install, headers=auth()).status_code == 200
    resolved = client.get(target, params={"require": "^1.0.0"}, headers=auth())
    assert resolved.status_code == 200
    assert resolved.json()["data"]["plugin_id"] == "hello-plugin"
    assert resolved.json()["meta"]["compatible"] is True
    assert client.get(target, params={"require": "^2.0.0"}, headers=auth()).status_code == 409
    assert client.get(target, params={"require": "invalid"}, headers=auth()).status_code == 422
    assert client.get(target, headers=auth("bob")).status_code == 404

    assert client.patch(install, headers=auth(), json={"enabled": False}).status_code == 200
    # No compatibility or version information leaks after disablement.
    assert client.get(target, params={"require": "^2.0.0"}, headers=auth()).status_code == 404
    assert not any(
        item["id"] == target.rsplit("/", 1)[-1]
        for item in client.get("/api/v1/capabilities", headers=auth()).json()["data"]
    )
    assert client.patch(install, headers=auth(), json={"enabled": True}).status_code == 200
    assert client.get(target, headers=auth()).status_code == 200
    original = settings.dev_tokens["alice"]["scope"]
    settings.dev_tokens["alice"]["scope"] = "desktop:read"
    assert client.get(target, headers=auth()).status_code == 404
    settings.dev_tokens["alice"]["scope"] = original
    assert client.delete(install, headers=auth()).status_code == 200
    assert client.get(target, headers=auth()).status_code == 404
