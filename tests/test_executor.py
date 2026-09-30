import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth
from .test_deploy import release


def test_trusted_executor_records_completion(settings, monkeypatch):
    client = TestClient(create_app("deploy-gateway", settings))
    rid = release(client)
    lease = client.post(
        "/api/v1/deploy/lease/acquire", headers=auth(), json={"agent_id": "alice", "release_id": rid}
    ).json()["data"]
    monkeypatch.setenv("DEPLOY_EXECUTOR_PATH", "/trusted/executor")

    def run(args, **kwargs):
        assert args == ["/trusted/executor"]
        job = json.loads(kwargs["input"])
        assert job["manifest"]["commit"] == "a" * 40
        return SimpleNamespace(returncode=0, stdout='{"state":"completed"}')

    monkeypatch.setattr("xeonolab.deploy.subprocess.run", run)
    result = client.post(
        "/api/v1/deployments",
        headers=auth(),
        json={"release_id": rid, "lease_id": lease["lease_id"], "epoch": lease["epoch"]},
    )
    assert result.json()["data"]["state"] == "completed"
    did = result.json()["data"]["deployment_id"]
    assert client.get("/api/v1/deployments/" + did, headers=auth()).json()["data"]["state"] == "completed"
    assert (
        client.post("/api/v1/deploy/lease/" + lease["lease_id"] + "/release", headers=auth()).status_code
        == 200
    )


def test_unconfirmed_executor_failure_freezes_lease(settings, monkeypatch):
    client = TestClient(create_app("deploy-gateway", settings))
    rid = release(client)
    lease = client.post(
        "/api/v1/deploy/lease/acquire", headers=auth(), json={"agent_id": "alice", "release_id": rid}
    ).json()["data"]
    monkeypatch.setenv("DEPLOY_EXECUTOR_PATH", "/trusted/executor")
    monkeypatch.setattr(
        "xeonolab.deploy.subprocess.run", lambda *a, **kw: SimpleNamespace(returncode=1, stdout="")
    )
    result = client.post(
        "/api/v1/deployments",
        headers=auth(),
        json={"release_id": rid, "lease_id": lease["lease_id"], "epoch": lease["epoch"]},
    )
    assert result.json()["data"]["state"] == "failed"
    assert (
        client.post("/api/v1/deploy/lease/" + lease["lease_id"] + "/release", headers=auth()).status_code
        == 409
    )
