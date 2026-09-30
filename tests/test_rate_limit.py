import json
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from fastapi.testclient import TestClient

from xeonolab.common import DEFAULT_RATE_LIMITS, Settings
from xeonolab.services import create_app


def make_settings(tmp_path):
    policies = {group: {"requests": 1, "window_seconds": 60} for group in DEFAULT_RATE_LIMITS}
    return Settings(
        Path(tmp_path),
        "development",
        dev_tokens={
            "alice": {"sub": "alice", "scope": "xelibrary:read xelibrary:admin", "agent_kind": "human"},
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
