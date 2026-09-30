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
