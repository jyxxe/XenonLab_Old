import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from xeonolab.common import connect
from xeonolab.desktop import create_app

from .conftest import auth


@pytest.fixture
def accounts(settings):
    settings.dev_tokens["admin"]["scope"] += (
        " desktop:admin desktop:read accounts:manage dev:read dev:write llm:use"
    )
    app = create_app(settings)

    class Provider:
        def __init__(self):
            self.users = {}

        def create(self, body):
            uid = str(uuid4())
            self.users[uid] = body.password
            settings.dev_tokens[uid] = {
                "sub": uid,
                "agent_kind": "human",
                "iat": time.time(),
                "scope": "desktop:read desktop:admin accounts:manage xelibrary:write",
            }
            return uid

        def password(self, uid, value):
            self.users[uid] = value

        def delete(self, uid):
            self.users.pop(uid)

    app.state.account_provider = Provider()
    client = TestClient(app)
    assert client.post("/api/v1/auth/accounts/bootstrap", headers=auth("admin"), json={}).status_code == 201
    return client, settings, app


def child(client, parent="admin", grants=None, actor="admin", inherit_all=False):
    result = client.post(
        "/api/v1/auth/accounts",
        headers=auth(actor),
        json={
            "parent_id": parent,
            "username": "user-" + uuid4().hex[:10],
            "name": "Child",
            "password": "temporary-test-password",
            "inherit_all": inherit_all,
            "grants": grants or ["desktop:read", "accounts:manage", "xelibrary:read"],
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["data"]["id"]


def scopes(client, token):
    result = client.get("/api/v1/auth/authorization", headers=auth(token))
    assert result.status_code == 200, result.text
    return set(result.json()["data"]["scopes"])


def test_tree_inheritance_sibling_boundary_and_live_shrink(accounts):
    client, settings, app = accounts
    uid = child(client)
    grand = child(client, uid, actor=uid, inherit_all=True)
    sibling = child(client)
    assert scopes(client, grand) == {"desktop:read", "accounts:manage", "xelibrary:read"}
    assert (
        client.patch(
            "/api/v1/auth/accounts/" + uid, headers=auth(uid), json={"inherit_all": True}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v1/auth/accounts/" + sibling, headers=auth(uid), json={"enabled": False}
        ).status_code
        == 403
    )
    visible = client.get("/api/v1/auth/accounts", headers=auth(uid)).json()["data"]
    assert {row["id"] for row in visible} == {uid, grand}
    assert (
        client.post(
            "/api/v1/auth/accounts",
            headers=auth(uid),
            json={
                "parent_id": uid,
                "username": "bad-privilege",
                "name": "Bad",
                "password": "temporary-test-password",
                "grants": ["desktop:admin"],
            },
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v1/auth/accounts/" + uid, headers=auth("admin"), json={"grants": ["desktop:read"]}
        ).status_code
        == 200
    )
    assert scopes(client, grand) == {"desktop:read"}
    assert scopes(client, uid) == {"desktop:read"}  # JWT's old broad claims cannot restore permissions.
    assert (
        client.patch(
            "/api/v1/auth/accounts/" + uid, headers=auth("admin"), json={"enabled": False}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/users/me", headers=auth(grand)).status_code == 401
    assert (
        client.patch(
            "/api/v1/auth/accounts/" + uid, headers=auth("admin"), json={"enabled": True}
        ).status_code
        == 200
    )
    assert scopes(client, grand) == {"desktop:read"}
    first = client.get("/api/v1/auth/accounts?limit=1", headers=auth("admin")).json()
    second = client.get(
        "/api/v1/auth/accounts",
        params={"limit": 100, "cursor": first["meta"]["next_cursor"]},
        headers=auth("admin"),
    ).json()
    assert len(first["data"]) + len(second["data"]) == 4


def test_api_key_attenuation_revocation_and_password_fencing(accounts):
    client, settings, app = accounts
    uid = child(client)
    created = client.post(
        "/api/v1/auth/api-keys",
        headers=auth(uid),
        json={
            "owner": uid,
            "name": "read-only",
            "scopes": ["desktop:read"],
        },
    )
    assert created.status_code == 201
    assert created.headers["cache-control"] == "no-store"
    key = created.json()["data"]["token"]
    kid = created.json()["data"]["id"]
    assert scopes(client, key) == {"desktop:read"}
    assert (
        client.post(
            "/api/v1/auth/api-keys",
            headers=auth(key),
            json={
                "owner": uid,
                "name": "expanded",
                "scopes": ["accounts:manage"],
            },
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/auth/accounts/" + uid + "/password",
            headers=auth(key),
            json={"password": "new-test-password"},
        ).status_code
        == 403
    )
    listed = client.get("/api/v1/auth/api-keys", headers=auth(uid), params={"owner": uid})
    assert key not in listed.text and "digest" not in listed.text
    with connect(settings.data_dir / "accounts.sqlite") as db:
        assert key not in json.dumps([dict(row) for row in db.execute("SELECT * FROM api_keys")])
    assert client.delete("/api/v1/auth/api-keys/" + kid, headers=auth("admin")).status_code == 200
    assert client.get("/api/v1/users/me", headers=auth(key)).status_code == 401
    key2 = client.post(
        "/api/v1/auth/api-keys",
        headers=auth(uid),
        json={
            "owner": uid,
            "name": "next",
            "scopes": ["desktop:read"],
        },
    ).json()["data"]["token"]
    assert (
        client.post(
            "/api/v1/auth/accounts/" + uid + "/password",
            headers=auth("admin"),
            json={"password": "new-test-password"},
        ).status_code
        == 200
    )
    assert app.state.account_provider.users[uid] == "new-test-password"
    assert client.get("/api/v1/users/me", headers=auth(uid)).status_code == 401
    assert client.get("/api/v1/users/me", headers=auth(key2)).status_code == 401
    settings.dev_tokens[uid]["iat"] = time.time() + 1
    assert client.get("/api/v1/users/me", headers=auth(uid)).status_code == 200
    audit = client.get("/api/v1/auth/accounts/audit/events", headers=auth("admin")).text
    assert "password_reset" in audit and "new-test-password" not in audit and key2 not in audit


def test_remote_service_checks_authority_and_fails_closed(accounts, monkeypatch):
    from xeonolab.services import create_app as service_app

    client, settings, app = accounts
    uid = child(client)
    key = client.post(
        "/api/v1/auth/api-keys",
        headers=auth(uid),
        json={
            "owner": uid,
            "name": "reader",
            "scopes": ["xelibrary:read"],
        },
    ).json()["data"]["token"]
    monkeypatch.setenv("AUTHORIZATION_URL", "https://authority.example/api/v1/auth/authorization")
    original_get = httpx.Client.get
    unavailable = False

    def get(instance, url, **kwargs):
        if isinstance(instance, TestClient):
            return original_get(instance, url, **kwargs)
        if unavailable:
            raise httpx.ConnectError("unavailable")
        result = client.get("/api/v1/auth/authorization", headers=kwargs["headers"])
        return httpx.Response(result.status_code, json=result.json(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", get)
    library = TestClient(service_app("xelibrary", settings))
    assert library.get("/api/v1/tables", headers=auth(key)).status_code == 200
    client.patch("/api/v1/auth/accounts/" + uid, headers=auth("admin"), json={"grants": ["desktop:read"]})
    assert library.get("/api/v1/tables", headers=auth(key)).status_code == 403
    unavailable = True
    assert library.get("/api/v1/tables", headers=auth(key)).status_code == 503


def test_self_password_reset_without_management_and_provider_failure(accounts):
    from xeonolab.common import APIError

    client, settings, app = accounts
    uid = child(client, grants=["desktop:read"])
    token = client.post(
        "/api/v1/auth/api-keys",
        headers=auth(uid),
        json={
            "owner": uid,
            "name": "reader",
            "scopes": ["desktop:read"],
        },
    ).json()["data"]["token"]

    def unavailable(uid, password):
        raise APIError(503, "ACCOUNT_PROVIDER_UNAVAILABLE", "unavailable")

    app.state.account_provider.password = unavailable
    result = client.post(
        "/api/v1/auth/accounts/" + uid + "/password",
        headers=auth(uid),
        json={"password": "new-test-password"},
    )
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "PASSWORD_RESET_PENDING"
    assert client.get("/api/v1/users/me", headers=auth(uid)).status_code == 401
    assert client.get("/api/v1/users/me", headers=auth(token)).status_code == 401
