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


def test_claim_openapi_documents_forgejo_sync_side_effects_and_failures(settings):
    contract = create_app("dev-agent", settings).openapi()["paths"]["/api/v1/work/{work_id}/claim"]["post"]

    assert "assigns the mapped Forgejo account" in contract["description"]
    assert "409" in contract["responses"]
    assert "503" in contract["responses"]


def test_claim_synchronizes_forgejo_assignee_and_comment(settings, monkeypatch, tmp_path):
    import json

    from xeonolab import work

    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    token_file = tmp_path / "forgejo-token"
    token_file.write_text("test-token")
    users_file = tmp_path / "forgejo-users.json"
    users_file.write_text(json.dumps({"alice": "general-agent"}))
    monkeypatch.setenv("FORGEJO_URL", "https://forgejo.example")
    monkeypatch.setenv("FORGEJO_CLAIM_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("FORGEJO_AGENT_USERS_FILE", str(users_file))

    class Reply:
        def __init__(self, value):
            self.value = value

        def raise_for_status(self):
            return None

        def json(self):
            return self.value

    class Client:
        def __init__(self, **kwargs):
            self.calls = []

        def get(self, url, **kwargs):
            self.calls.append(("get", url, kwargs))
            return Reply({"number": 73, "state": "open", "assignees": [{"login": "triage"}]})

        def patch(self, url, **kwargs):
            self.calls.append(("patch", url, kwargs))
            return Reply({})

        def post(self, url, **kwargs):
            self.calls.append(("post", url, kwargs))
            return Reply({"id": 12})

        def close(self):
            return None

    client = Client()
    monkeypatch.setattr(work.httpx, "Client", lambda **kwargs: client)
    api = TestClient(create_app("dev-agent", settings))
    item = api.post(
        "/api/v1/work", headers=auth(),
        json={"repository": "xenon-admin/XeonoLab", "issue_id": 73, "title": "Harness claims"},
    ).json()["data"]
    result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth("alice"), json={})

    assert result.status_code == 200
    assert [call[0] for call in client.calls] == ["get", "patch", "post"]
    assert client.calls[1][2]["json"] == {"assignees": ["triage", "general-agent"]}
    assert client.calls[2][2]["json"]["body"] == (
        "Harness claim: @general-agent claimed this Issue (Work epoch 1)."
    )


def test_claim_sync_failure_releases_claim_and_restores_assignees(settings, monkeypatch, tmp_path):
    import json

    import httpx

    from xeonolab import work

    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    token_file = tmp_path / "forgejo-token"
    token_file.write_text("test-token")
    users_file = tmp_path / "forgejo-users.json"
    users_file.write_text(json.dumps({"alice": "general-agent"}))
    monkeypatch.setenv("FORGEJO_URL", "https://forgejo.example")
    monkeypatch.setenv("FORGEJO_CLAIM_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("FORGEJO_AGENT_USERS_FILE", str(users_file))

    class Reply:
        def __init__(self, value=None, fail=False):
            self.value, self.fail = value, fail

        def raise_for_status(self):
            if self.fail:
                request = httpx.Request("POST", "https://forgejo.example/comments")
                response = httpx.Response(503, request=request)
                raise httpx.HTTPStatusError("provider unavailable", request=request, response=response)

        def json(self):
            return self.value

    class Client:
        def __init__(self, **kwargs):
            self.patches = []

        def get(self, url, **kwargs):
            return Reply({"number": 73, "state": "open", "assignees": [{"login": "triage"}]})

        def patch(self, url, **kwargs):
            self.patches.append(kwargs["json"])
            return Reply({})

        def post(self, url, **kwargs):
            return Reply(fail=True)

        def close(self):
            return None

    client = Client()
    monkeypatch.setattr(work.httpx, "Client", lambda **kwargs: client)
    api = TestClient(create_app("dev-agent", settings))
    item = api.post(
        "/api/v1/work", headers=auth(),
        json={"repository": "xenon-admin/XeonoLab", "issue_id": 73, "title": "Harness claims"},
    ).json()["data"]
    result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth("alice"), json={})

    assert result.status_code == 503
    current = next(row for row in api.get("/api/v1/work", headers=auth()).json()["data"] if row["id"] == item["id"])
    assert current["status"] == "queued" and current["holder"] is None and current["epoch"] == 2
    assert client.patches == [
        {"assignees": ["triage", "general-agent"]},
        {"assignees": ["triage"]},
    ]


def test_claim_setup_failures_are_safe_api_errors_and_release_claim(settings, monkeypatch, tmp_path):
    import json

    from xeonolab import work

    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    token_file = tmp_path / "forgejo-token"
    token_file.write_text("test-token")
    users_file = tmp_path / "forgejo-users.json"
    users_file.write_text(json.dumps({"alice": "general-agent"}))
    monkeypatch.setenv("FORGEJO_URL", "https://forgejo.example")
    monkeypatch.setenv("FORGEJO_CLAIM_TOKEN_FILE", str(token_file))
    monkeypatch.setenv("FORGEJO_AGENT_USERS_FILE", str(users_file))

    for index, failure in enumerate(("missing-ca", "invalid-ca", "client-constructor"), start=73):
        api = TestClient(create_app("dev-agent", settings))
        item = api.post(
            "/api/v1/work", headers=auth(),
            json={"repository": "xenon-admin/XeonoLab", "issue_id": index, "title": "Harness claims"},
        ).json()["data"]
        if failure == "missing-ca":
            monkeypatch.setenv("FORGEJO_CA_FILE", str(tmp_path / "missing-ca.pem"))
        elif failure == "invalid-ca":
            bad_ca = tmp_path / "invalid-ca.pem"
            bad_ca.write_text("not a certificate")
            monkeypatch.setenv("FORGEJO_CA_FILE", str(bad_ca))
        else:
            monkeypatch.delenv("FORGEJO_CA_FILE", raising=False)
            monkeypatch.setattr(
                work.httpx, "Client", lambda **kwargs: (_ for _ in ()).throw(RuntimeError("setup failure"))
            )

        result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth("alice"), json={})
        assert result.status_code == 503
        assert "test-token" not in result.text
        current = next(
            row for row in api.get("/api/v1/work", headers=auth()).json()["data"] if row["id"] == item["id"]
        )
        assert current["status"] == "queued" and current["holder"] is None and current["epoch"] == 2


def test_claim_does_not_return_success_if_fenced_during_forgejo_sync(settings, monkeypatch):
    import time

    from xeonolab import work
    from xeonolab.common import transaction

    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    api = TestClient(create_app("dev-agent", settings))
    item = api.post(
        "/api/v1/work", headers=auth(),
        json={"repository": "xenon-admin/XeonoLab", "issue_id": 73, "title": "Harness claims"},
    ).json()["data"]

    def concurrent_reclaim(claimed, actor, current_settings):
        with transaction(current_settings.data_dir / "work.sqlite") as db:
            db.execute(
                "UPDATE work SET status='claimed',holder=?,expires=?,epoch=? WHERE id=?",
                ("contender", time.time() + 300, claimed["epoch"] + 1, claimed["id"]),
            )

    monkeypatch.setattr(work, "sync_forgejo_claim", concurrent_reclaim)
    result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth("alice"), json={})

    assert result.status_code == 409
    assert result.json()["error"]["code"] == "STALE_CLAIM"
    current = next(row for row in api.get("/api/v1/work", headers=auth()).json()["data"] if row["id"] == item["id"])
    assert current["status"] == "claimed" and current["holder"] == "contender" and current["epoch"] == 2


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
    claimed = client.post(path + "/claim", headers=auth(), json={}).json()["data"]
    epoch = claimed["epoch"]
    renewed = client.post(
        path + "/renew", headers=auth(), json={"epoch": epoch, "ttl_seconds": 3600}
    )
    assert renewed.status_code == 200 and renewed.json()["data"]["expires"] > claimed["expires"]
    assert event("b", "closed", "2026-09-30T11:00:00Z").status_code == 200
    current = client.get("/api/v1/work", headers=auth()).json()["data"][0]
    assert current["status"] == "completed" and current["epoch"] == epoch + 1
    assert current["holder"] is None and current["expires"] == 0
    assert client.post(path + "/renew", headers=auth(), json={"epoch": epoch}).status_code == 409
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


def test_renew_extends_active_claim_and_fences_stale_or_expired_holders(settings, monkeypatch):
    from xeonolab import work
    from xeonolab.common import connect

    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    clock = [2000.0]
    monkeypatch.setattr(work.time, "time", lambda: clock[0])
    client = TestClient(create_app("dev-agent", settings))
    item = client.post(
        "/api/v1/work",
        headers=auth(),
        json={"repository": "owner/repo", "issue_id": 3, "title": "Renewal test"},
    ).json()["data"]
    path = "/api/v1/work/" + item["id"]
    claimed = client.post(path + "/claim", headers=auth(), json={"ttl_seconds": 30}).json()["data"]
    assert claimed["holder"] == "alice" and claimed["expires"] == 2030.0
    clock[0] += 20

    # Missing dev:write scope, another holder, a stale epoch, TTL bounds and 404 are all rejected.
    assert (
        client.post(path + "/renew", headers=auth("opencode"), json={"epoch": claimed["epoch"]}).status_code
        == 403
    )
    assert (
        client.post(path + "/renew", headers=auth("bob"), json={"epoch": claimed["epoch"]}).status_code == 409
    )
    assert (
        client.post(path + "/renew", headers=auth(), json={"epoch": claimed["epoch"] + 1}).status_code == 409
    )
    assert (
        client.post(
            path + "/renew", headers=auth(), json={"epoch": claimed["epoch"], "ttl_seconds": 10}
        ).status_code
        == 422
    )
    assert client.post("/api/v1/work/missing/renew", headers=auth(), json={"epoch": 1}).status_code == 404

    renewed = client.post(
        path + "/renew", headers=auth(), json={"epoch": claimed["epoch"], "ttl_seconds": 60}
    ).json()["data"]
    assert renewed["expires"] == 2080.0
    assert renewed["epoch"] == claimed["epoch"] and renewed["holder"] == "alice"
    with connect(settings.data_dir / "work.sqlite") as db:
        assert db.execute("SELECT count(*) FROM work_audit WHERE event='work.renewed'").fetchone()[0] == 1

    # Past the original expiry but inside the renewed window the claim is still exclusive,
    # and the renewal survives a process restart.
    clock[0] += 40
    assert client.post(path + "/claim", headers=auth("bob"), json={}).status_code == 409
    restarted = TestClient(create_app("dev-agent", settings))
    assert (
        restarted.post(
            path + "/renew", headers=auth(), json={"epoch": claimed["epoch"], "ttl_seconds": 30}
        ).status_code
        == 200
    )
    assert restarted.post(path + "/release", headers=auth(), json={"epoch": claimed["epoch"]}).status_code == 200

    # An expired lease cannot renew; the next claim bumps the epoch and fences the old holder.
    second = client.post(path + "/claim", headers=auth(), json={"ttl_seconds": 30}).json()["data"]
    clock[0] += 31
    assert client.post(path + "/renew", headers=auth(), json={"epoch": second["epoch"]}).status_code == 409
    third = client.post(path + "/claim", headers=auth("bob"), json={}).json()["data"]
    assert third["epoch"] == second["epoch"] + 1
    assert client.post(path + "/release", headers=auth(), json={"epoch": second["epoch"]}).status_code == 409
