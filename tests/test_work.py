from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth


def test_claim_fencing_persistence_and_permissions(settings):
    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    client = TestClient(create_app("dev-agent", settings))
    assert client.get("/api/v1/work").status_code == 401
    assert client.get("/api/v1/work", headers=auth("admin")).status_code == 403
    body = {"repository": "xenon-admin/XeonoLab", "issue_id": 1, "title": "Implement queue"}
    item = client.post("/api/v1/work", headers=auth(), json=body).json()["data"]
    assert client.post("/api/v1/work", headers=auth(), json=body).json()["data"]["id"] == item["id"]
    path = "/api/v1/work/" + item["id"]

    def claim(user):
        return user, client.post(path + "/claim", headers=auth(user), json={"ttl_seconds": 30})

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(claim, ("alice", "bob")))
    assert sorted(result.status_code for _, result in results) == [200, 409]
    owner, result = next((u, r) for u, r in results if r.status_code == 200)
    epoch = result.json()["data"]["epoch"]
    other = "bob" if owner == "alice" else "alice"
    restarted = TestClient(create_app("dev-agent", settings))
    assert restarted.post(path + "/release", headers=auth(other), json={"epoch": epoch}).status_code == 409
    assert (
        restarted.post(path + "/release", headers=auth(owner), json={"epoch": epoch + 1}).status_code == 409
    )
    assert restarted.post(path + "/release", headers=auth(owner), json={"epoch": epoch}).status_code == 200
    second = restarted.post(path + "/claim", headers=auth(other), json={}).json()["data"]
    assert second["epoch"] == epoch + 1
    assert restarted.post(path + "/release", headers=auth(owner), json={"epoch": epoch}).status_code == 409


def test_expired_claim_cannot_complete_reassigned_task(settings, monkeypatch):
    from xeonolab import work

    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    clock = [1000.0]
    monkeypatch.setattr(work.time, "time", lambda: clock[0])
    client = TestClient(create_app("dev-agent", settings))
    item = client.post(
        "/api/v1/work",
        headers=auth(),
        json={"repository": "owner/repo", "issue_id": 2, "title": "Recovery test"},
    ).json()["data"]
    path = "/api/v1/work/" + item["id"]
    first = client.post(path + "/claim", headers=auth(), json={"ttl_seconds": 30}).json()["data"]
    clock[0] += 31
    assert (
        client.post(
            path + "/release", headers=auth(), json={"epoch": first["epoch"], "completed": True}
        ).status_code
        == 409
    )
    second = client.post(path + "/claim", headers=auth("bob"), json={}).json()["data"]
    assert second["epoch"] == first["epoch"] + 1
    assert (
        client.post(
            path + "/release", headers=auth("bob"), json={"epoch": second["epoch"], "completed": True}
        ).status_code
        == 200
    )
    assert client.post(path + "/claim", headers=auth(), json={}).status_code == 409


def test_issue_links_are_scoped_persistent_and_cycle_safe(settings):
    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    client = TestClient(create_app("dev-agent", settings))
    for number in (1, 2):
        client.post(
            "/api/v1/work",
            headers=auth(),
            json={"repository": "owner/repo", "issue_id": number, "title": "Task"},
        )
    body = {"repository": "owner/repo", "target_issue_id": 2, "type": "duplicate_of"}
    assert client.post("/api/v1/issues/1/links", headers=auth("bob"), json=body).status_code == 403
    assert client.post("/api/v1/issues/1/links", headers=auth(), json=body).status_code == 201
    assert (
        client.post("/api/v1/issues/2/links", headers=auth(), json={**body, "target_issue_id": 1}).status_code
        == 409
    )
    assert (
        client.post("/api/v1/issues/1/links", headers=auth(), json={**body, "target_issue_id": 1}).status_code
        == 422
    )
    assert (
        client.post("/api/v1/issues/1/links", headers=auth(), json={**body, "target_issue_id": 3}).status_code
        == 404
    )
    restarted = TestClient(create_app("dev-agent", settings))
    assert (
        len(
            restarted.get(
                "/api/v1/issues/2/links", headers=auth(), params={"repository": "owner/repo"}
            ).json()["data"]
        )
        == 1
    )
    assert (
        restarted.get("/api/v1/issues/2/links", headers=auth(), params={"repository": "other/repo"}).json()[
            "data"
        ]
        == []
    )


def test_signed_issue_sync_is_atomic_ordered_and_fences_closed_claim(settings, monkeypatch):
    import hashlib
    import hmac
    import json

    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    monkeypatch.setenv("FORGEJO_WEBHOOK_SECRET", "test-secret")
    client = TestClient(create_app("dev-agent", settings))

    def event(delivery, state, updated, repository="xenon-admin/XeonoLab"):
        raw = json.dumps(
            {
                "repository": {"full_name": repository},
                "issue": {"number": 42, "title": "Actual Issue", "state": state, "updated_at": updated},
            }
        ).encode()
        return client.post(
            "/api/v1/webhooks/forgejo",
            content=raw,
            headers={
                "X-Forgejo-Delivery": delivery,
                "X-Forgejo-Event": "issues",
                "X-Forgejo-Signature": hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest(),
            },
        )

    assert event("a", "open", "2026-09-30T10:00:00Z").status_code == 200
    assert event("a", "open", "2026-09-30T10:00:00Z").json()["data"]["duplicate"]
    items = client.get("/api/v1/work", headers=auth()).json()["data"]
    assert len(items) == 1
    path = "/api/v1/work/" + items[0]["id"]
    epoch = client.post(path + "/claim", headers=auth(), json={}).json()["data"]["epoch"]
    assert event("b", "closed", "2026-09-30T11:00:00Z").status_code == 200
    assert client.post(path + "/release", headers=auth(), json={"epoch": epoch}).status_code == 409
    assert event("c", "open", "2026-09-30T10:30:00Z").status_code == 200
    assert client.get("/api/v1/work", headers=auth()).json()["data"][0]["status"] == "completed"
    assert event("d", "open", "2026-09-30T12:00:00Z").status_code == 200
    assert client.get("/api/v1/work", headers=auth()).json()["data"][0]["status"] == "queued"
    assert event("e", "open", "2026-09-30T12:00:00Z", "other/repo").status_code == 403
    assert event("e", "open", "2026-09-30T12:00:00Z").status_code == 200


def test_legacy_delivery_history_survives_event_store_migration(settings, monkeypatch):
    import hashlib
    import hmac

    from xeonolab.common import transaction

    monkeypatch.setenv("FORGEJO_WEBHOOK_SECRET", "test-secret")
    raw = b"{}"
    with transaction(settings.data_dir / "events.sqlite") as db:
        db.execute("CREATE TABLE events(id TEXT PRIMARY KEY,digest TEXT,payload TEXT)")
        db.execute("INSERT INTO events VALUES(?,?,?)", ("old", hashlib.sha256(raw).hexdigest(), "{}"))
    client = TestClient(create_app("dev-agent", settings))
    result = client.post(
        "/api/v1/webhooks/forgejo",
        content=raw,
        headers={
            "X-Forgejo-Delivery": "old",
            "X-Forgejo-Signature": hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest(),
        },
    )
    assert result.status_code == 200 and result.json()["data"]["duplicate"] is True
    assert (settings.data_dir / "events.sqlite").exists()


def test_equal_timestamp_conflict_blocks_until_source_reconciliation(settings, monkeypatch):
    import hashlib
    import hmac
    import json

    import httpx

    from xeonolab import work

    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    monkeypatch.setenv("FORGEJO_WEBHOOK_SECRET", "test-secret")
    monkeypatch.setenv("FORGEJO_URL", "https://forgejo.example")
    client = TestClient(create_app("dev-agent", settings))

    def event(delivery, state):
        raw = json.dumps(
            {
                "repository": {"full_name": "xenon-admin/XeonoLab"},
                "issue": {
                    "number": 7,
                    "title": "Same second",
                    "state": state,
                    "updated_at": "2026-09-30T10:00:00Z",
                },
            }
        ).encode()
        return client.post(
            "/api/v1/webhooks/forgejo",
            content=raw,
            headers={
                "X-Forgejo-Event": "issues",
                "X-Forgejo-Delivery": delivery,
                "X-Forgejo-Signature": hmac.new(b"test-secret", raw, hashlib.sha256).hexdigest(),
            },
        )

    assert event("first", "open").status_code == 200
    item = client.get("/api/v1/work", headers=auth()).json()["data"][0]
    path = "/api/v1/work/" + item["id"]
    first = client.post(path + "/claim", headers=auth(), json={}).json()["data"]
    assert event("second", "closed").status_code == 200
    assert client.post(path + "/claim", headers=auth(), json={}).status_code == 409
    assert client.post(path + "/release", headers=auth(), json={"epoch": first["epoch"]}).status_code == 409
    assert client.get("/api/v1/work", headers=auth()).json()["data"][0]["status"] == "blocked"

    class Source:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url):
            assert url == "https://forgejo.example/api/v1/repos/xenon-admin/XeonoLab/issues/7"
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "number": 7,
                    "title": "Current source",
                    "state": "open",
                    "updated_at": "2026-09-30T10:00:00Z",
                },
            )

    monkeypatch.setattr(work.httpx, "Client", Source)
    result = client.post(
        "/api/v1/issues/7/reconcile", headers=auth(), json={"repository": "xenon-admin/XeonoLab"}
    )
    assert result.status_code == 200 and result.json()["data"]["status"] == "queued"
    assert result.json()["data"]["title"] == "Current source"
    assert client.post(path + "/claim", headers=auth(), json={}).status_code == 200
