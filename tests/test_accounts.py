import json
import time
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from xeonolab.common import connect
from xeonolab.desktop import create_app
from xeonolab.role_scopes import OIDC_ROLE_SCOPES

from .conftest import auth, put_acl


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

        def password(self, uid, value, epoch):
            self.users[uid] = value
            settings.dev_tokens[uid]["next_epoch"] = epoch

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


def test_account_tree_first_use_requires_explicit_bootstrap_and_preserves_scopes(settings):
    settings.dev_tokens["admin"].update(
        scope="desktop:read desktop:admin",
        agent_kind="human",
    )
    settings.dev_tokens["viewer"] = {
        "sub": "viewer",
        "scope": "desktop:read",
        "agent_kind": "human",
    }
    client = TestClient(create_app(settings))

    unregistered = client.get("/api/v1/auth/accounts", headers=auth("admin"))
    assert unregistered.status_code == 404
    assert unregistered.json()["error"]["code"] == "ACCOUNT_NOT_FOUND"
    assert client.post("/api/v1/auth/accounts/bootstrap", headers=auth("viewer"), json={}).status_code == 403
    with connect(settings.data_dir / "accounts.sqlite") as db:
        assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 0

    created = client.post("/api/v1/auth/accounts/bootstrap", headers=auth("admin"), json={})
    assert created.status_code == 201, created.text
    assert set(created.json()["data"]["effective_scopes"]) == {"desktop:admin", "desktop:read"}
    assert "accounts:manage" not in created.json()["data"]["effective_scopes"]


def test_platform_admin_oidc_role_can_explicitly_grant_embedding_to_a_human_child(accounts):
    client, settings, _app = accounts
    root_before = client.get("/api/v1/auth/accounts", headers=auth("admin")).json()["data"][0]
    assert not {"embedding:read", "embedding:write"} & set(root_before["effective_scopes"])

    # This models refreshed human OIDC tokens after the platform-admin mapper is
    # updated. The registered root record does not inherit these scopes implicitly.
    settings.role_scopes = OIDC_ROLE_SCOPES
    settings.dev_tokens["admin"]["realm_access"] = {"roles": ["platform-admin"]}
    assert not {"embedding:read", "embedding:write"} & scopes(client, "admin")

    candidates = client.get("/api/v1/auth/accounts/root-scope-candidates", headers=auth("admin"))
    assert candidates.status_code == 200, candidates.text
    assert set(candidates.json()["data"]["candidate_scopes"]) >= {
        "embedding:read", "embedding:write"
    }

    explicit_grants = sorted(set(root_before["grants"]) | {"embedding:read", "embedding:write"})
    updated = client.patch(
        "/api/v1/auth/accounts/admin", headers=auth("admin"), json={"grants": explicit_grants}
    )
    assert updated.status_code == 200, updated.text
    assert {"embedding:read", "embedding:write"} <= set(updated.json()["data"]["effective_scopes"])
    remaining_candidates = client.get(
        "/api/v1/auth/accounts/root-scope-candidates", headers=auth("admin")
    ).json()["data"]["candidate_scopes"]
    assert not {"embedding:read", "embedding:write"} & set(remaining_candidates)

    child_id = child(
        client,
        grants=["desktop:read", "embedding:read", "embedding:write"],
        actor="admin",
    )
    assert {"embedding:read", "embedding:write"} <= scopes(client, child_id)
    assert client.get(
        "/api/v1/auth/accounts/root-scope-candidates", headers=auth(child_id)
    ).status_code == 403


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


def test_shrunk_parent_scope_does_not_block_name_or_enabled_patch(accounts):
    client, _, _ = accounts
    uid = child(
        client,
        grants=["desktop:read", "accounts:manage", "xelibrary:read", "xelibrary:write"],
    )
    tree = client.get("/api/v1/auth/accounts", headers=auth("admin")).json()["data"]
    root = next(row for row in tree if row["id"] == "admin")
    assert "xelibrary:write" in root["grants"]

    revoked = sorted(set(root["grants"]) - {"xelibrary:write"})
    response = client.patch(
        "/api/v1/auth/accounts/admin", headers=auth("admin"), json={"grants": revoked}
    )
    assert response.status_code == 200, response.text

    child_before = next(
        row for row in client.get("/api/v1/auth/accounts", headers=auth("admin")).json()["data"]
        if row["id"] == uid
    )
    assert "xelibrary:write" in child_before["grants"]
    assert "xelibrary:write" not in child_before["effective_scopes"]

    renamed = client.patch(
        f"/api/v1/auth/accounts/{uid}", headers=auth("admin"), json={"name": "Renamed child"}
    )
    assert renamed.status_code == 200, renamed.text
    disabled = client.patch(
        f"/api/v1/auth/accounts/{uid}", headers=auth("admin"), json={"enabled": False}
    )
    assert disabled.status_code == 200, disabled.text
    enabled = client.patch(
        f"/api/v1/auth/accounts/{uid}", headers=auth("admin"), json={"enabled": True}
    )
    assert enabled.status_code == 200, enabled.text

    explicit_old_grant = client.patch(
        f"/api/v1/auth/accounts/{uid}",
        headers=auth("admin"),
        json={"grants": ["desktop:read", "xelibrary:read", "xelibrary:write"]},
    )
    assert explicit_old_grant.status_code == 403
    assert explicit_old_grant.json()["error"]["code"] == "PERMISSION_ESCALATION"

    removed = client.patch(
        f"/api/v1/auth/accounts/{uid}",
        headers=auth("admin"),
        json={"grants": ["desktop:read", "xelibrary:read"]},
    )
    assert removed.status_code == 200, removed.text
    assert "xelibrary:write" not in removed.json()["data"]["grants"]


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
    root_key_result = client.post(
        "/api/v1/auth/api-keys",
        headers=auth("admin"),
        json={"owner": "admin", "name": "root-policy-key", "scopes": ["desktop:admin"]},
    )
    assert root_key_result.status_code == 201, root_key_result.text
    root_key = root_key_result.json()["data"]["token"]
    resolved_key = app.state.authorization_resolver(root_key)
    assert resolved_key.scopes == {"desktop:admin"}
    assert resolved_key.token_scopes is None  # DB key scopes are not verified JWT claims.
    assert client.get(
        "/api/v1/auth/accounts/root-scope-candidates", headers=auth(root_key)
    ).status_code == 403
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
    # Fresh credentials work even with the exact same issued-at timestamp.
    settings.dev_tokens[uid]["account_epoch"] = settings.dev_tokens[uid]["next_epoch"]
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

    def unavailable(uid, password, epoch):
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


def test_resource_acl_inherits_parent_and_child_entry_overrides(accounts, monkeypatch):
    from xeonolab.services import create_app as service_app

    client, settings, app = accounts
    uid = child(client, grants=["desktop:read", "accounts:manage", "xelibrary:read", "xelibrary:write"])
    grand = child(client, uid, actor=uid, grants=["desktop:read", "xelibrary:read"])
    monkeypatch.setenv("AUTHORIZATION_URL", "https://authority.example/api/v1/auth/authorization")
    original_get = httpx.Client.get

    def get(instance, url, **kwargs):
        if isinstance(instance, TestClient):
            return original_get(instance, url, **kwargs)
        result = client.get("/api/v1/auth/authorization", headers=kwargs["headers"])
        return httpx.Response(result.status_code, json=result.json(), request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx.Client, "get", get)
    library = TestClient(service_app("xelibrary", settings))
    created = library.post(
        "/api/v1/tables",
        headers=auth(uid),
        json={
            "path": "/account-inheritance",
            "name": "Owned by parent",
            "columns": [{"name": "title", "type": "string"}],
        },
    )
    assert created.status_code == 201
    tid = created.json()["data"]["table_id"]
    assert library.get("/api/v1/tables/" + tid, headers=auth(grand)).status_code == 200
    assert (
        library.post(
            "/api/v1/tables/" + tid + "/rows", headers=auth(grand), json={"values": {"title": "forbidden"}}
        ).status_code
        == 403
    )
    assert put_acl(library, "table", tid, {uid: ["read"]}).status_code == 200
    assert library.get("/api/v1/tables/" + tid, headers=auth(grand)).status_code == 200
    assert put_acl(library, "table", tid, {uid: ["read"], grand: []}).status_code == 200
    assert library.get("/api/v1/tables/" + tid, headers=auth(grand)).status_code == 403


def test_password_provider_preserves_profile_while_updating_credential_version():
    from xeonolab.accounts import Keycloak

    provider = Keycloak()
    calls = []
    uid = str(uuid4())
    profile = {
        "id": uid,
        "username": "child",
        "firstName": "First",
        "lastName": "Last",
        "email": "child@xenon.lan",
        "emailVerified": True,
        "enabled": True,
        "attributes": {"existing": ["retained"]},
    }

    def request(method, path, body=None):
        calls.append((method, path, body))
        return httpx.Response(200, json=profile)

    provider.request = request
    provider.password(uid, "temporary-test-password", "new-version")
    updated = calls[1][2]
    assert updated["firstName"] == "First" and updated["lastName"] == "Last"
    assert updated["email"] == profile["email"] and updated["emailVerified"] is True
    assert updated["attributes"] == {"existing": ["retained"], "xeonolab_credential_epoch": ["new-version"]}
    assert calls[2][1].endswith("/reset-password") and calls[3][1].endswith("/logout")
