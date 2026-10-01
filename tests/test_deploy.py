import time
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from xeonolab.common import transaction
from xeonolab.services import create_app

from .conftest import auth


def release(client):
    result = client.post(
        "/api/v1/releases",
        headers=auth(),
        json={
            "commit": "a" * 40,
            "artifacts": [{"name": "xelibrary", "digest": "sha256:" + "b" * 64}],
            "migration_manifest": "none",
            "test_report": "test-suite-passed",
        },
    )
    rid = result.json()["data"]["release_id"]
    assert client.post(f"/api/v1/releases/{rid}/approve", headers=auth()).status_code == 403
    assert client.post(f"/api/v1/releases/{rid}/approve", headers=auth("admin")).status_code == 200
    return rid


def test_lease_exclusion_and_identity(settings):
    client = TestClient(create_app("deploy-gateway", settings))
    rid = release(client)
    assert (
        client.post(
            "/api/v1/deploy/lease/acquire",
            headers=auth("opencode"),
            json={"agent_id": "opencode", "release_id": rid},
        ).status_code
        == 403
    )

    def acquire(who):
        return client.post(
            "/api/v1/deploy/lease/acquire", headers=auth(who), json={"agent_id": who, "release_id": rid}
        )

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(acquire, ["alice", "bob"]))
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(r.json()["data"] for r in responses if r.status_code == 200)
    assert (
        client.post(
            "/api/v1/deployments",
            headers=auth(winner["holder"]),
            json={"release_id": rid, "lease_id": winner["lease_id"], "epoch": winner["epoch"]},
        ).status_code
        == 503
    )
    with transaction(settings.data_dir / "deploy.sqlite") as db:
        db.execute("UPDATE lease SET expires=?", (time.time() - 1,))
        db.execute(
            "INSERT INTO deployments VALUES('d',?,?,?,'deploying')", (rid, winner["holder"], winner["epoch"])
        )
    assert acquire("alice").status_code == 409


def test_dedicated_maintainer_identity_uses_same_exclusive_lease(settings):
    client = TestClient(create_app("deploy-gateway", settings))
    rid = release(client)
    lease = client.post(
        "/api/v1/deploy/lease/acquire",
        headers=auth("devagent"),
        json={"agent_id": "devagent", "release_id": rid},
    )
    assert lease.status_code == 200
    lease = lease.json()["data"]
    assert (
        client.post(
            "/api/v1/deploy/lease/acquire",
            headers=auth("alice"),
            json={"agent_id": "alice", "release_id": rid},
        ).status_code
        == 409
    )
    assert (
        client.post(f"/api/v1/deploy/lease/{lease['lease_id']}/release", headers=auth("devagent")).status_code
        == 200
    )
