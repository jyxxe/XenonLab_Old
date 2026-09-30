import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import httpx
from fastapi import Depends
from fastapi.testclient import TestClient

from xeonolab.common import DEFAULT_RATE_LIMITS, Settings, base_app, identity
from xeonolab.embedding import create_app as create_embedding_app
from xeonolab.library import create_app as create_library_app
from xeonolab.services import create_app


def make_settings(tmp_path):
    policies = {group: {"requests": 1, "window_seconds": 60} for group in DEFAULT_RATE_LIMITS}
    return Settings(
        Path(tmp_path),
        "development",
        dev_tokens={
            "alice": {
                "sub": "alice",
                "scope": "xelibrary:read xelibrary:query xelibrary:admin",
                "agent_kind": "human",
            },
            "bob": {"sub": "bob", "scope": "xelibrary:read", "agent_kind": "human"},
        },
        rate_limit_policies=policies,
    )


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_rate_limit_isolated_by_identity_and_route_group_and_health_is_exempt(tmp_path, caplog):
    client = TestClient(create_app("xelibrary", make_settings(tmp_path)))
    caplog.set_level(logging.WARNING, logger="xeonolab.audit")

    assert client.get("/api/v1/tables", headers=auth("alice")).status_code == 200
    limited = client.get("/api/v1/tables", headers=auth("alice"))
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert limited.json()["error"]["code"] == "RATE_LIMITED"

    # The same route group has a separate bucket for each authenticated subject.
    assert client.get("/api/v1/tables", headers=auth("bob")).status_code == 200
    # An admin route has its own bucket even for an identity that hit interactive.
    assert client.get("/api/v1/acl", headers=auth("alice")).status_code == 200
    # Unauthenticated health endpoints do not use API identity buckets.
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200

    events = [json.loads(record.message) for record in caplog.records if "rate_limit.rejected" in record.message]
    assert len(events) == 1
    assert events[0]["route_group"] == "interactive"
    assert events[0]["identity_hash"] != "alice"
    assert events[0]["retry_after"] == 60


def test_production_defaults_enable_group_policies(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("APP_MODE", "production")
    monkeypatch.delenv("RATE_LIMIT_ENABLED", raising=False)
    monkeypatch.delenv("RATE_LIMIT_POLICIES_JSON", raising=False)

    settings = Settings.environment("xelibrary")

    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_policies == DEFAULT_RATE_LIMITS
    assert settings.rate_limit_policies["embedding-quality"]["requests"] < settings.rate_limit_policies[
        "embedding-fast"
    ]["requests"]


def test_policy_override_changes_one_group_and_keeps_other_defaults(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RATE_LIMIT_POLICIES_JSON", '{"interactive":{"requests":9}}')

    settings = Settings.environment("xelibrary")

    assert settings.rate_limit_policies["interactive"] == {"requests": 9, "window_seconds": 60}
    assert settings.rate_limit_policies["admin"] == DEFAULT_RATE_LIMITS["admin"]


def test_concurrent_requests_cannot_both_spend_the_last_slot(tmp_path):
    client = TestClient(create_app("xelibrary", make_settings(tmp_path)))
    barrier = Barrier(2)

    def request_tables():
        barrier.wait()
        return client.get("/api/v1/tables", headers=auth("alice")).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(lambda _: request_tables(), range(2)))

    assert sorted(statuses) == [200, 429]


def test_rate_limit_can_be_disabled_explicitly(tmp_path):
    settings = make_settings(tmp_path)
    settings.rate_limit_enabled = False
    client = TestClient(create_app("xelibrary", settings))

    assert client.get("/api/v1/tables", headers=auth("alice")).status_code == 200
    assert client.get("/api/v1/tables", headers=auth("alice")).status_code == 200


def test_short_group_cleanup_does_not_erase_long_window_bucket(tmp_path, monkeypatch):
    from xeonolab import common

    settings = make_settings(tmp_path)
    settings.rate_limit_policies["interactive"]["window_seconds"] = 120
    settings.rate_limit_policies["admin"]["window_seconds"] = 5
    now = [1000.0]
    monkeypatch.setattr(common.time, "time", lambda: now[0])
    client = TestClient(create_library_app(settings))

    assert client.get("/api/v1/tables", headers=auth("alice")).status_code == 200
    assert client.get("/api/v1/acl", headers=auth("alice")).status_code == 200
    now[0] += 10
    # A later short-window request may clean expired admin records only.
    assert client.get("/api/v1/acl", headers=auth("alice")).status_code == 200
    assert client.get("/api/v1/tables", headers=auth("alice")).status_code == 429


def test_internal_authorization_endpoint_is_exempt_from_user_buckets(tmp_path):
    settings = make_settings(tmp_path)
    app = base_app("account-authority", settings)

    @app.get("/api/v1/auth/authorization")
    def authorization(actor=Depends(identity)):
        return {"subject": actor.sub}

    client = TestClient(app)

    assert all(client.get("/api/v1/auth/authorization", headers=auth("alice")).status_code == 200 for _ in range(4))
    from xeonolab.common import connect

    with connect(app.state.rate_limit_db) as db:
        assert db.execute("SELECT count(*) FROM rate_limit_requests").fetchone()[0] == 0


def test_downstream_service_preserves_authority_429_and_retry_after(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTHORIZATION_URL", "https://authority.example/api/v1/auth/authorization")
    settings = make_settings(tmp_path)
    original_get = httpx.Client.get

    def limited_authority(_client, url, **kwargs):
        if isinstance(_client, TestClient):
            return original_get(_client, url, **kwargs)
        return httpx.Response(
            429,
            headers={"Retry-After": "17"},
            request=httpx.Request("GET", url),
        )

    monkeypatch.setattr(httpx.Client, "get", limited_authority)
    client = TestClient(create_library_app(settings))
    response = client.get("/api/v1/tables", headers=auth("xek_example"))

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "17"
    assert response.json()["error"]["code"] == "AUTHORIZATION_RATE_LIMITED"


def test_semantic_search_routes_fast_and_quality_modes_to_separate_buckets(tmp_path):
    settings = make_settings(tmp_path)
    settings.rate_limit_policies["embedding-quality"]["requests"] = 1
    client = TestClient(create_library_app(settings))

    quality = {**auth("alice"), "Content-Type": "application/json"}
    quality_body = {"query": "q", "mode": "quality"}
    fast_body = {"query": "q", "mode": "fast"}
    assert client.post("/api/v1/search/semantic", headers=quality, json=quality_body).status_code == 503
    assert client.post("/api/v1/search/semantic", headers=quality, json=fast_body).status_code == 503
    blocked = client.post("/api/v1/search/semantic", headers=quality, json=quality_body)
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"] == "60"


def test_embeddings_route_quality_model_to_quality_bucket(tmp_path):
    settings = make_settings(tmp_path)
    settings.dev_tokens["alice"]["scope"] = "embedding:write"
    settings.rate_limit_policies["embedding-quality"]["requests"] = 1
    client = TestClient(create_embedding_app(settings))
    headers = auth("alice")

    quality_body = {"model": "quality", "input": "q"}
    fast_body = {"model": "fast", "input": "q"}
    assert client.post("/v1/embeddings", headers=headers, json=quality_body).status_code == 503
    assert client.post("/v1/embeddings", headers=headers, json=fast_body).status_code == 503
    blocked = client.post("/v1/embeddings", headers=headers, json=quality_body)
    assert blocked.status_code == 429
