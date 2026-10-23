import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
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
    conflict = contract["responses"]["409"]["description"]
    unavailable = contract["responses"]["503"]["description"]
    assert "STALE_CLAIM" in conflict and "error` envelope" in conflict
    assert "FORGEJO_CLAIM_NOT_CONFIGURED" in unavailable
    assert "FORGEJO_CLAIM_SYNC_FAILED" in unavailable
    assert "conditionally reset only while that exact claim remains current" in unavailable
    assert "newer state is preserved" in unavailable
    assert "only the comment ID created by this request" in conflict
    assert "assignees are left untouched" in conflict
    assert "reconciliation_required" in unavailable
    assert "no compare-and-swap protection" in contract["description"]


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
        "Harness claim: @general-agent claimed this Issue (Work epoch 1). "
        "Verify the active holder and epoch via the Work API before working."
    )


def test_claim_sync_failure_releases_claim_without_restoring_assignees(settings, monkeypatch, tmp_path):
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
    ]
    assert result.json()["error"]["details"] == {
        "work_id": item["id"],
        "claim_epoch": 1,
        "forgejo_sync": {
            "assignee": "added", "comment": "unknown", "comment_id": None,
            "reconciliation_required": True,
        },
    }


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


@pytest.fixture
def forgejo_claim_transport(settings, monkeypatch, tmp_path):
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
    original_client = work.httpx.Client

    def configure(handler):
        monkeypatch.setattr(
            work.httpx, "Client",
            lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
        )

    return configure


@pytest.mark.parametrize("fence_at", ["PATCH", "POST"])
@pytest.mark.parametrize("state", ["closed", "blocked", "other_holder", "same_holder", "expired"])
@pytest.mark.parametrize("delete_fails", [False, True])
def test_stale_sync_deletes_only_own_comment_and_preserves_shared_assignees(
    settings, forgejo_claim_transport, fence_at, state, delete_fails,
):
    import json
    import time

    import httpx

    from xeonolab.common import transaction

    app = create_app("dev-agent", settings)
    api = TestClient(app)
    item = api.post(
        "/api/v1/work", headers=auth(),
        json={"repository": "xenon-admin/XeonoLab", "issue_id": 73, "title": "Harness claims"},
    ).json()["data"]
    assignees, comments, calls = ["triage"], {13: "new holder's comment"}, []

    def fence():
        with transaction(settings.data_dir / "work.sqlite") as db:
            if state == "closed":
                app.state.ingest_forgejo(db, {
                    "repository": {"full_name": item["repository"]},
                    "issue": {"number": 73, "title": item["title"], "state": "closed",
                              "updated_at": "2026-10-01T09:00:00Z"},
                })
            else:
                status = "blocked" if state == "blocked" else "claimed"
                holder = None if state == "blocked" else "bob" if state == "other_holder" else "alice"
                db.execute(
                    "UPDATE work SET status=?,holder=?,epoch=?,expires=? WHERE id=?",
                    (status, holder, 1 if state == "expired" else 2,
                     0 if state == "expired" else time.time() + 300, item["id"]),
                )
        # Concurrent remote assignments must survive both successful and failed cleanup.
        assignees.extend(["new-holder", "human-new"])

    def remote(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json={
                "number": 73, "state": "open", "assignees": [{"login": name} for name in assignees],
            })
        if request.method == "PATCH":
            assignees[:] = json.loads(request.content)["assignees"]
            if fence_at == "PATCH":
                fence()
            return httpx.Response(201, json={})
        if request.method == "POST":
            comments[12] = json.loads(request.content)["body"]
            if fence_at == "POST":
                fence()
            return httpx.Response(201, json={"id": 12})
        assert request.method == "DELETE"
        assert request.url.path == "/api/v1/repos/xenon-admin/XeonoLab/issues/comments/12"
        # Cleanup permits concurrent local writes and must not hold the SQLite transaction.
        with transaction(settings.data_dir / "work.sqlite") as db:
            db.execute("UPDATE work SET title='cleanup concurrent update' WHERE id=?", (item["id"],))
        if delete_fails:
            return httpx.Response(403, text="provider detail test-token")
        del comments[12]
        return httpx.Response(204)

    forgejo_claim_transport(remote)
    result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth(), json={})

    assert result.status_code == 409
    assert result.json()["error"]["code"] == "STALE_CLAIM"
    assert result.json()["error"]["details"] == {
        "work_id": item["id"], "claim_epoch": 1,
        "forgejo_sync": {
            "assignee": "added", "comment": "cleanup_failed" if delete_fails else "deleted",
            "comment_id": 12, "reconciliation_required": True,
        },
    }
    assert "test-token" not in result.text
    assert [method for method, _ in calls] == ["GET", "PATCH", "POST", "DELETE"]
    assert assignees == ["triage", "general-agent", "new-holder", "human-new"]
    assert 13 in comments and (12 in comments) == delete_fails
    current = next(row for row in api.get("/api/v1/work", headers=auth()).json()["data"] if row["id"] == item["id"])
    assert current["title"] == "cleanup concurrent update"
    assert current["epoch"] == (1 if state == "expired" else 2)
    assert current["status"] == {"closed": "completed", "blocked": "blocked"}.get(state, "claimed")
    assert current["holder"] == (None if state in ("closed", "blocked") else "bob" if state == "other_holder" else "alice")


@pytest.mark.parametrize("issue_fields", [{"assignees": None}, {"assignees": []}, {}], ids=["null", "empty", "missing"])
def test_claim_synchronizes_unassigned_forgejo_issue(settings, forgejo_claim_transport, issue_fields):
    import json

    import httpx

    calls = []

    def remote(request):
        calls.append((request.method, request.url.path, json.loads(request.content) if request.content else None))
        if request.method == "GET":
            return httpx.Response(200, json={
                "number": 73, "state": "open", "assignee": None, "milestone": None,
                "labels": None, "pull_request": None, **issue_fields,
            })
        if request.method == "PATCH":
            return httpx.Response(201, json={})
        assert request.method == "POST"
        return httpx.Response(201, json={"id": 12})

    forgejo_claim_transport(remote)
    api = TestClient(create_app("dev-agent", settings))
    item = api.post(
        "/api/v1/work", headers=auth(),
        json={"repository": "xenon-admin/XeonoLab", "issue_id": 73, "title": "Harness claims"},
    ).json()["data"]
    result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth(), json={})

    assert result.status_code == 200
    assert result.json()["data"]["holder"] == "alice" and result.json()["data"]["epoch"] == 1
    assert [method for method, _, _ in calls] == ["GET", "PATCH", "POST"]
    assert calls[1][1:] == ("/api/v1/repos/xenon-admin/XeonoLab/issues/73", {"assignees": ["general-agent"]})
    assert calls[2][1] == "/api/v1/repos/xenon-admin/XeonoLab/issues/73/comments"
    assert calls[2][2]["body"] == (
        "Harness claim: @general-agent claimed this Issue (Work epoch 1). "
        "Verify the active holder and epoch via the Work API before working."
    )


@pytest.mark.parametrize("failure", ["patch_timeout", "post_timeout", "missing_comment_id", "invalid_comment_id"])
def test_ambiguous_remote_writes_do_not_overwrite_new_claim_or_guess_comment_id(
    settings, forgejo_claim_transport, failure,
):
    import json
    import time

    import httpx

    from xeonolab.common import transaction

    api = TestClient(create_app("dev-agent", settings))
    item = api.post(
        "/api/v1/work", headers=auth(),
        json={"repository": "xenon-admin/XeonoLab", "issue_id": 73, "title": "Harness claims"},
    ).json()["data"]
    assignees, calls = ["triage"], []

    def remote(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"number": 73, "state": "open", "assignees": [{"login": "triage"}]})
        if request.method == "PATCH":
            assignees[:] = json.loads(request.content)["assignees"]
            if failure != "patch_timeout":
                return httpx.Response(201, json={})
        # This newer claim and remote assignments must not be rolled back on any failure.
        with transaction(settings.data_dir / "work.sqlite") as db:
            db.execute(
                "UPDATE work SET holder='bob',epoch=2,expires=? WHERE id=?",
                (time.time() + 300, item["id"]),
            )
        assignees.extend(["new-holder", "human-new"])
        if failure.endswith("timeout"):
            raise httpx.ReadTimeout("provider detail test-token", request=request)
        assert request.method == "POST"
        return httpx.Response(201, json={} if failure == "missing_comment_id" else {"id": True})

    forgejo_claim_transport(remote)
    result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth(), json={})

    assert result.status_code == 503
    assert result.json()["error"]["code"] == "FORGEJO_CLAIM_SYNC_FAILED"
    assert result.json()["error"]["details"]["forgejo_sync"] == {
        "assignee": "unknown" if failure == "patch_timeout" else "added",
        "comment": "not_attempted" if failure == "patch_timeout" else "unknown",
        "comment_id": None, "reconciliation_required": True,
    }
    assert "test-token" not in result.text
    assert calls == (["GET", "PATCH"] if failure == "patch_timeout" else ["GET", "PATCH", "POST"])
    assert assignees == ["triage", "general-agent", "new-holder", "human-new"]
    current = next(row for row in api.get("/api/v1/work", headers=auth()).json()["data"] if row["id"] == item["id"])
    assert current["holder"] == "bob" and current["epoch"] == 2 and current["status"] == "claimed"


@pytest.mark.parametrize("delete_fails", [False, True])
def test_stale_sync_preserves_preexisting_assignee(settings, forgejo_claim_transport, delete_fails):
    import httpx

    from xeonolab.common import transaction

    api = TestClient(create_app("dev-agent", settings))
    item = api.post(
        "/api/v1/work", headers=auth(),
        json={"repository": "xenon-admin/XeonoLab", "issue_id": 73, "title": "Harness claims"},
    ).json()["data"]
    calls = []

    def remote(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={
                "number": 73, "state": "open", "assignees": [{"login": "general-agent"}],
            })
        if request.method == "POST":
            with transaction(settings.data_dir / "work.sqlite") as db:
                db.execute("UPDATE work SET epoch=epoch+1 WHERE id=?", (item["id"],))
            return httpx.Response(201, json={"id": 12})
        assert request.method == "DELETE"
        return httpx.Response(403 if delete_fails else 204)

    forgejo_claim_transport(remote)
    result = api.post(f"/api/v1/work/{item['id']}/claim", headers=auth(), json={})

    assert result.status_code == 409
    assert calls == ["GET", "POST", "DELETE"]
    assert result.json()["error"]["details"]["forgejo_sync"] == {
        "assignee": "unchanged", "comment": "cleanup_failed" if delete_fails else "deleted",
        "comment_id": 12, "reconciliation_required": delete_fails,
    }


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


def test_admin_complete_closes_queue_hygiene_gaps(settings):
    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    client = TestClient(create_app("dev-agent", settings))

    def create(issue_id, title):
        body = {"repository": "xenon-admin/XeonoLab", "issue_id": issue_id, "title": title}
        return client.post("/api/v1/work", headers=auth(), json=body).json()["data"]

    stale = create(401, "Abandoned item")
    live = create(402, "Actively held item")
    done = create(403, "Already completed")
    client.post(f"/api/v1/work/{live['id']}/claim", headers=auth(), json={"ttl_seconds": 3600})
    client.post(f"/api/v1/work/{done['id']}/claim", headers=auth(), json={"ttl_seconds": 60})
    client.post(f"/api/v1/work/{done['id']}/release", headers=auth(), json={"epoch": 1, "completed": True})

    # unheld queued item: admin complete succeeds and fences via epoch+1
    result = client.post(f"/api/v1/work/{stale['id']}/admin-complete", headers=auth())
    assert result.status_code == 200, result.text
    assert result.json()["data"]["status"] == "completed"
    assert result.json()["data"]["holder"] is None
    assert result.json()["data"]["epoch"] == stale["epoch"] + 1
    # idempotency guard
    assert client.post(f"/api/v1/work/{stale['id']}/admin-complete", headers=auth()).status_code == 409

    # actively held claim must not be completed by an administrator
    conflict = client.post(f"/api/v1/work/{live['id']}/admin-complete", headers=auth())
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "WORK_CLAIMED"
    # already completed
    assert client.post(f"/api/v1/work/{done['id']}/admin-complete", headers=auth()).status_code == 409
    # missing item
    assert client.post("/api/v1/work/missing/admin-complete", headers=auth()).status_code == 404
    # unauthenticated
    assert client.post(f"/api/v1/work/{stale['id']}/admin-complete").status_code == 401
    # expired claim CAN be completed (queue hygiene goal): claim then age it out
    expired = create(404, "Expired claim item")
    client.post(f"/api/v1/work/{expired['id']}/claim", headers=auth(), json={"ttl_seconds": 3600})
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.execute("UPDATE work SET expires=? WHERE id=?", (time.time() - 5, expired["id"]))
        db.commit()
    result = client.post(f"/api/v1/work/{expired['id']}/admin-complete", headers=auth())
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "completed"
    # audit trail records the administrative close-out
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.row_factory = sqlite3.Row
        events = [dict(r)["event"] for r in db.execute(
            "SELECT event FROM work_audit WHERE work_id=?", (expired["id"],))]
    assert "work.admin_completed" in events


def test_admin_complete_rejects_blocked_and_persists_closeout(settings):
    """Issue #91 review: blocked items must be reconciled first, and an admin
    close-out survives newer open-webhook events for its issue."""
    settings.dev_tokens["alice"]["scope"] += " dev:read dev:write"
    client = TestClient(create_app("dev-agent", settings))

    def create(issue_id, title):
        body = {"repository": "xenon-admin/XeonoLab", "issue_id": issue_id, "title": title}
        return client.post("/api/v1/work", headers=auth(), json=body).json()["data"]

    conflicted = create(411, "Source conflict item")
    closeout = create(412, "Admin closed-out item")
    reopenable = create(413, "Reopen after close")

    # blocked (source conflict) items are refused until reconciled
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.execute("UPDATE work SET status='blocked',holder=NULL,expires=0 WHERE id=?", (conflicted["id"],))
        db.commit()
    refusal = client.post(f"/api/v1/work/{conflicted['id']}/admin-complete", headers=auth())
    assert refusal.status_code == 409
    assert refusal.json()["error"]["code"] == "SYNC_CONFLICT"

    # a close-out with a reason persists across newer open-webhook events
    done = client.post(
        f"/api/v1/work/{closeout['id']}/admin-complete", headers=auth(), json={"reason": "已由 PR #99 交付"}
    )
    assert done.status_code == 200 and done.json()["data"]["status"] == "completed"
    ingest_webhook = client.app.state.ingest_forgejo

    def issue_payload(number, title, state, stamp):
        return {
            "repository": {"full_name": "xenon-admin/XeonoLab"},
            "issue": {"number": number, "title": title, "state": state, "updated_at": stamp},
        }
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.row_factory = sqlite3.Row
        ingest_webhook(db, issue_payload(412, "Admin closed-out item", "open", "2026-10-02T12:00:00Z"))
    still = client.get("/api/v1/work", headers=auth()).json()["data"]
    row = next(item for item in still if item["id"] == closeout["id"])
    assert row["status"] == "completed" and row["admin_completed"] == 1
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.row_factory = sqlite3.Row
        events = [dict(r)["event"] for r in db.execute(
            "SELECT event FROM work_audit WHERE work_id=?", (closeout["id"],))]
    assert any(event.startswith("work.admin_completed") and "PR #99" in event for event in events)
    assert "issue.sync_ignored_admin_completed" in events

    # without the admin flag, a newer open event re-queues a completed item
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.execute("UPDATE work SET status='completed',holder=NULL,expires=0,admin_completed=0 WHERE id=?", (reopenable["id"],))
        db.commit()
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.row_factory = sqlite3.Row
        ingest_webhook(db, issue_payload(413, "Reopen after close", "open", "2026-10-02T12:05:00Z"))
    row = next(item for item in client.get("/api/v1/work", headers=auth()).json()["data"] if item["id"] == reopenable["id"])
    assert row["status"] == "queued"

    # a Forgejo close clears the admin flag so a later reopen re-queues normally
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.row_factory = sqlite3.Row
        ingest_webhook(db, issue_payload(412, "Admin closed-out item", "closed", "2026-10-02T12:10:00Z"))
        ingest_webhook(db, issue_payload(412, "Admin closed-out item", "open", "2026-10-02T12:15:00Z"))
    row = next(item for item in client.get("/api/v1/work", headers=auth()).json()["data"] if item["id"] == closeout["id"])
    assert row["status"] == "queued" and row["admin_completed"] == 0


def test_listing_active_filter_and_lease_expired(settings):
    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    client = TestClient(create_app("dev-agent", settings))

    def create(issue_id, title):
        body = {"repository": "xenon-admin/XeonoLab", "issue_id": issue_id, "title": title}
        return client.post("/api/v1/work", headers=auth(), json=body).json()["data"]["id"]

    create(101, "Queued item")
    live, expired, blocked = (create(102, "Live claim"),
                              create(103, "Expired claim"), create(104, "Blocked item"))
    assert client.post(f"/api/v1/work/{live}/claim", headers=auth(), json={"ttl_seconds": 3600}).status_code == 200
    assert client.post(f"/api/v1/work/{expired}/claim", headers=auth(), json={"ttl_seconds": 3600}).status_code == 200
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.execute("UPDATE work SET expires=? WHERE id=?", (time.time() - 100, expired))
        db.execute("UPDATE work SET status='blocked',holder=NULL,expires=0 WHERE id=?", (blocked,))
        db.commit()

    unfiltered = client.get("/api/v1/work?limit=100", headers=auth()).json()["data"]
    by_title = {row["title"]: row for row in unfiltered}
    assert set(by_title) == {"Queued item", "Live claim", "Expired claim", "Blocked item"}
    assert by_title["Expired claim"]["lease_expired"] is True
    assert by_title["Expired claim"]["claim_active"] is False
    for title in ("Queued item", "Live claim", "Blocked item"):
        assert by_title[title]["lease_expired"] is False, title
    assert by_title["Live claim"]["claim_active"] is True
    # claim_active stays the live-lease contract: a queued item has no holder,
    # so it must remain claimable in the UI (regression for PR #102 review).
    assert by_title["Queued item"]["claim_active"] is False
    assert by_title["Blocked item"]["claim_active"] is False

    active_ids = {row["title"] for row in
                  client.get("/api/v1/work?active=true&limit=100", headers=auth()).json()["data"]}
    assert active_ids == {"Queued item", "Live claim"}
    inactive_ids = {row["title"] for row in
                    client.get("/api/v1/work?active=false&limit=100", headers=auth()).json()["data"]}
    assert inactive_ids == {"Expired claim", "Blocked item"}

    # work rows are uuid-keyed, so page order across items is not assumed (#92)
    page = client.get("/api/v1/work?active=true&limit=1", headers=auth()).json()
    assert len(page["data"]) == 1
    first = page["data"][0]["title"]
    assert first in {"Queued item", "Live claim"}
    assert page["meta"]["next_cursor"]
    follow = client.get(
        f"/api/v1/work?active=true&limit=1&cursor={page['meta']['next_cursor']}", headers=auth()
    ).json()
    remaining = ({"Queued item", "Live claim"} - {first}).pop()
    assert [row["title"] for row in follow["data"]] == [remaining]
    assert follow["meta"]["next_cursor"] is None

    assert client.get("/api/v1/work?active=maybe", headers=auth()).status_code == 422
    error = client.get("/api/v1/work?active=maybe", headers=auth()).json()["error"]
    assert error["code"] == "INVALID_ACTIVE"


def test_listing_orders_fifo_filters_and_counts(settings):
    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    client = TestClient(create_app("dev-agent", settings))

    def create(issue_id, title, repository="xenon-admin/XeonoLab"):
        body = {"repository": repository, "issue_id": issue_id, "title": title}
        return client.post("/api/v1/work", headers=auth(), json=body).json()["data"]["id"]

    legacy = create(201, "Legacy item")
    create(202, "Second item")
    create(203, "Third item")
    other = create(204, "Other repo item", repository="other/Repo")
    with sqlite3.connect(settings.data_dir / "work.sqlite") as db:
        db.execute("UPDATE work SET created_at=0 WHERE id=?", (legacy,))
        db.commit()

    page1 = client.get("/api/v1/work?limit=2", headers=auth()).json()
    assert [row["title"] for row in page1["data"]] == ["Legacy item", "Second item"], "legacy rows sort first (FIFO)"
    assert page1["data"][0]["created_at"] == 0
    assert page1["data"][1]["created_at"] > 0
    assert page1["meta"]["counts"] == {"queued": 4, "claimed": 0, "completed": 0, "blocked": 0}
    repeat = client.get("/api/v1/work?limit=2", headers=auth()).json()
    assert [row["id"] for row in repeat["data"]] == [row["id"] for row in page1["data"]], "order is deterministic"

    follow = client.get(
        f"/api/v1/work?limit=2&cursor={page1['meta']['next_cursor']}", headers=auth()
    ).json()
    assert [row["title"] for row in follow["data"]] == ["Third item", "Other repo item"]
    assert follow["meta"]["next_cursor"] is None

    assert client.post(f"/api/v1/work/{other}/claim", headers=auth(), json={"ttl_seconds": 3600}).status_code == 200
    claimed = {row["title"] for row in client.get("/api/v1/work?status=claimed", headers=auth()).json()["data"]}
    assert claimed == {"Other repo item"}
    multi = {row["title"] for row in
             client.get("/api/v1/work?status=queued,claimed&limit=100", headers=auth()).json()["data"]}
    assert multi == {"Legacy item", "Second item", "Third item", "Other repo item"}
    assert client.get("/api/v1/work?status=queued,nope", headers=auth()).json()["error"]["code"] == "INVALID_STATUS"
    assert client.get("/api/v1/work?cursor=not-a-cursor", headers=auth()).json()["error"]["code"] == "INVALID_CURSOR"

    other_only = {row["title"] for row in
                  client.get("/api/v1/work?repository=other/Repo", headers=auth()).json()["data"]}
    assert other_only == {"Other repo item"}
    by_issue = {row["title"] for row in client.get("/api/v1/work?issue_id=202", headers=auth()).json()["data"]}
    assert by_issue == {"Second item"}


def test_created_at_survives_reconcile_and_restart(settings):
    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " dev:read dev:write"
    client = TestClient(create_app("dev-agent", settings))
    body = {"repository": "xenon-admin/XeonoLab", "issue_id": 301, "title": "Persisted item"}
    created = client.post("/api/v1/work", headers=auth(), json=body).json()["data"]
    assert created["created_at"] > 0
    reopened = TestClient(create_app("dev-agent", settings))
    row = reopened.get("/api/v1/work?issue_id=301", headers=auth()).json()["data"][0]
    assert row["created_at"] == created["created_at"], "migrated/legacy columns persist across restart"
