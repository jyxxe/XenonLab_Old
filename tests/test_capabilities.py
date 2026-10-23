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
    assert client.get("/api/v1/capabilities/builtin.platform.app.knowledge", headers=auth("alice")).status_code == 200
    assert client.get("/api/v1/capabilities/builtin.platform.missing", headers=auth("alice")).status_code == 404

    disk = tmp_path / "bad.json"
    disk.write_text(json.dumps(manifest(api_version="9")))
    monkeypatch.setattr(capabilities_module, "load_registry", lambda: load_registry(disk))
    broken = client.get("/api/v1/capabilities", headers=auth("alice"))
    assert broken.status_code == 503
    assert broken.json()["error"]["code"] == "CAPABILITY_REGISTRY_INVALID"
