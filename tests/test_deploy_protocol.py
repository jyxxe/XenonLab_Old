"""The platform deployment protocol must not discard unsupported constraints."""

import copy
import io
import json
import runpy
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth
from .test_deploy import release

MANIFEST = {
    "commit": "a" * 40,
    "artifacts": [{"name": "platform-release", "digest": "sha256:" + "b" * 64}],
    "migration_manifest": "none",
    "test_report": "synthetic regression",
}
UNSUPPORTED = [
    ("service_plan_digest", "sha256:" + "c" * 64),
    ("policy_revision", 1),
    ("trust_revision", 1),
    ("generation", 1),
    ("admission_granted", True),
]


def records(settings, table):
    with sqlite3.connect(settings.data_dir / "deploy.sqlite") as db:
        return db.execute("SELECT count(*) FROM " + table).fetchone()[0]


@pytest.mark.parametrize("field,value", UNSUPPORTED)
def test_release_rejects_unknown_required_constraints_without_creating_records(settings, field, value):
    api = TestClient(create_app("deploy-gateway", settings))
    result = api.post("/api/v1/releases", headers=auth(), json={**MANIFEST, field: value})
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "VALIDATION_ERROR"
    assert records(settings, "releases") == records(settings, "audit") == 0


@pytest.mark.parametrize(
    "field,value", [("url", "https://untrusted.invalid/image"), ("service_plan_digest", "synthetic-secret")]
)
def test_artifact_extensions_are_not_discarded_or_echoed(settings, field, value):
    api = TestClient(create_app("deploy-gateway", settings))
    body = copy.deepcopy(MANIFEST)
    body["artifacts"][0][field] = value
    result = api.post("/api/v1/releases", headers=auth(), json=body)
    assert result.status_code == 422
    assert value not in result.text
    assert records(settings, "releases") == records(settings, "audit") == 0


def test_lease_and_deployment_inputs_reject_unknown_fields_before_state_changes(settings, monkeypatch):
    api = TestClient(create_app("deploy-gateway", settings))
    rid = release(api)
    audit = records(settings, "audit")
    body = {"agent_id": "alice", "release_id": rid}
    result = api.post("/api/v1/deploy/lease/acquire", headers=auth(), json={**body, "generation": 7})
    assert result.status_code == 422 and records(settings, "audit") == audit
    lease = api.post("/api/v1/deploy/lease/acquire", headers=auth(), json=body).json()["data"]
    audit = records(settings, "audit")
    result = api.post(
        f"/api/v1/deploy/lease/{lease['lease_id']}/renew",
        headers=auth(),
        json={"ttl_seconds": 60, "service_plan_digest": "synthetic-secret"},
    )
    assert result.status_code == 422 and records(settings, "audit") == audit
    calls = []
    monkeypatch.setenv("DEPLOY_EXECUTOR_PATH", "/synthetic/executor")
    monkeypatch.setattr("xeonolab.deploy.subprocess.run", lambda *args, **kwargs: calls.append(args))
    result = api.post(
        "/api/v1/deployments",
        headers=auth(),
        json={
            "release_id": rid,
            "lease_id": lease["lease_id"],
            "epoch": lease["epoch"],
            "policy_revision": 1,
        },
    )
    assert result.status_code == 422 and not calls
    assert records(settings, "deployments") == 0 and records(settings, "audit") == audit


@pytest.mark.parametrize("mutation", ["top-level", "artifact", "malformed"])
def test_approved_stored_contract_is_revalidated_before_dispatch(settings, monkeypatch, mutation):
    api = TestClient(create_app("deploy-gateway", settings), raise_server_exceptions=False)
    rid = release(api)
    lease = api.post(
        "/api/v1/deploy/lease/acquire", headers=auth(), json={"agent_id": "alice", "release_id": rid}
    ).json()["data"]
    with sqlite3.connect(settings.data_dir / "deploy.sqlite") as db:
        manifest = json.loads(db.execute("SELECT manifest FROM releases WHERE id=?", (rid,)).fetchone()[0])
        if mutation == "top-level":
            manifest["service_plan_digest"] = "sha256:" + "c" * 64
        if mutation == "artifact":
            manifest["artifacts"][0]["url"] = "https://untrusted.invalid/image"
        raw = "{not-json" if mutation == "malformed" else json.dumps(manifest)
        db.execute("UPDATE releases SET manifest=? WHERE id=?", (raw, rid))
    audit, calls = records(settings, "audit"), []
    monkeypatch.setenv("DEPLOY_EXECUTOR_PATH", "/synthetic/executor")

    def executor(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout='{"state":"completed"}')

    monkeypatch.setattr("xeonolab.deploy.subprocess.run", executor)
    result = api.post(
        "/api/v1/deployments",
        headers=auth(),
        json={"release_id": rid, "lease_id": lease["lease_id"], "epoch": lease["epoch"]},
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "RELEASE_CONTRACT_UNSUPPORTED"
    assert not calls and records(settings, "deployments") == 0
    assert records(settings, "audit") == audit


def run_fixed_executor(monkeypatch, job, ssh_results=None):
    """Execute the real receiver script with all config/SSH effects intercepted."""
    script = Path(__file__).resolve().parents[1] / "infra/systemd/executor.py"
    calls, reads = [], []
    results = iter(ssh_results) if ssh_results is not None else None
    real_read = Path.read_text

    def read(path, *args, **kwargs):
        if path == Path("/etc/xeonolab/executor.json"):
            reads.append(str(path))
            return json.dumps(
                {
                    "commit": MANIFEST["commit"],
                    "artifacts": MANIFEST["artifacts"],
                    "targets": ["synthetic-target"],
                }
            )
        return real_read(path, *args, **kwargs)

    def ssh(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=next(results) if results is not None else 0)

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr("subprocess.run", ssh)
    monkeypatch.setattr(
        "sys.stdin",
        io.TextIOWrapper(io.BytesIO((json.dumps(job) if isinstance(job, dict) else job).encode("utf-8"))),
    )
    try:
        runpy.run_path(str(script), run_name="__main__")
    except SystemExit as error:
        return error.code, calls, reads
    return 0, calls, reads


@pytest.mark.parametrize("location", ["job", "manifest", "artifact", "oversize"])
def test_fixed_executor_rejects_unsupported_contract_before_config_or_ssh(monkeypatch, location):
    job = {"manifest": copy.deepcopy(MANIFEST), "deployment_id": "d" * 36, "epoch": 1}
    if location == "job":
        job["service_plan_digest"] = "sha256:" + "c" * 64
    if location == "manifest":
        job["manifest"]["policy_revision"] = 1
    if location == "artifact":
        job["manifest"]["artifacts"][0]["service_plan_digest"] = "sha256:" + "c" * 64
    if location == "oversize":
        job = json.dumps(job) + " " * 65536
    code, calls, reads = run_fixed_executor(monkeypatch, job)
    assert code == 2 and not calls and not reads


def test_fixed_executor_preserves_current_platform_protocol(monkeypatch, capsys):
    job = {"manifest": MANIFEST, "deployment_id": "d" * 36, "epoch": 1}
    code, calls, reads = run_fixed_executor(monkeypatch, job)
    assert code == 0 and len(calls) == 1 and len(reads) == 1
    assert calls[0][-1] == "activate " + "a" * 40 + " " + "d" * 36
    assert calls[0][0] == "ssh" and calls[0][-2] == "xeonolab-deploy@synthetic-target"
    assert json.loads(capsys.readouterr().out) == {"state": "completed"}


def test_openapi_explicitly_forbids_unknown_deploy_fields(settings):
    schemas = create_app("deploy-gateway", settings).openapi()["components"]["schemas"]
    for name in ("Release", "Artifact", "Acquire", "Renew", "Deployment", "EmptyAction"):
        assert schemas[name]["additionalProperties"] is False


@pytest.mark.parametrize("results,code,state", [([1, 0], 0, "rolled_back"), ([1, 1], 1, "failed")])
def test_fixed_executor_keeps_rollback_confirmation_contract(monkeypatch, capsys, results, code, state):
    job = {"manifest": MANIFEST, "deployment_id": "d" * 36, "epoch": 1}
    actual, calls, reads = run_fixed_executor(monkeypatch, job, results)
    assert actual == code and len(reads) == 1 and len(calls) == 2
    assert calls[-1][-1] == "rollback " + "d" * 36
    assert json.loads(capsys.readouterr().out) == {"state": state}


@pytest.mark.parametrize(
    "location,value",
    [
        ("epoch", True),
        ("epoch", 0),
        ("epoch", "1"),
        ("manifest", []),
        ("deployment_id", None),
    ],
)
def test_receiver_invalid_known_field_types_deny_before_inventory(monkeypatch, location, value):
    job = {"manifest": copy.deepcopy(MANIFEST), "deployment_id": "d" * 36, "epoch": 1}
    job[location] = value
    code, calls, reads = run_fixed_executor(monkeypatch, job)
    assert code == 2 and not calls and not reads


def test_gateway_dispatches_valid_serialization_through_real_receiver_contract(settings, monkeypatch):
    from contextlib import redirect_stdout

    api = TestClient(create_app("deploy-gateway", settings))
    result = api.post("/api/v1/releases", headers=auth(), json=MANIFEST)
    assert result.status_code == 201
    rid = result.json()["data"]["release_id"]
    assert api.post(f"/api/v1/releases/{rid}/approve", headers=auth("admin")).status_code == 200
    lease = api.post(
        "/api/v1/deploy/lease/acquire", headers=auth(), json={"agent_id": "alice", "release_id": rid}
    ).json()["data"]
    monkeypatch.setenv("DEPLOY_EXECUTOR_PATH", "/synthetic/executor")
    observed = []

    def executor(args, **kwargs):
        assert args == ["/synthetic/executor"]
        job = json.loads(kwargs["input"])
        assert job["manifest"] == MANIFEST
        with monkeypatch.context() as local, redirect_stdout(io.StringIO()) as output:
            code, calls, reads = run_fixed_executor(local, job)
        observed.append((code, calls, reads))
        return SimpleNamespace(returncode=code, stdout=output.getvalue())

    monkeypatch.setattr("xeonolab.deploy.subprocess.run", executor)
    result = api.post(
        "/api/v1/deployments",
        headers=auth(),
        json={
            "release_id": rid,
            "lease_id": lease["lease_id"],
            "epoch": lease["epoch"],
        },
    )
    assert result.status_code == 200 and result.json()["data"]["state"] == "completed"
    assert len(observed) == 1 and observed[0][0] == 0 and len(observed[0][1]) == 1
    assert records(settings, "deployments") == 1


@pytest.mark.parametrize("action", ["approve", "release"])
def test_empty_actions_reject_unsupported_constraints_without_mutation(settings, action):
    api = TestClient(create_app("deploy-gateway", settings))
    rid = api.post("/api/v1/releases", headers=auth(), json=MANIFEST).json()["data"]["release_id"]
    lease = None
    if action == "approve":
        target, actor = f"/api/v1/releases/{rid}/approve", "admin"
    else:
        assert api.post(f"/api/v1/releases/{rid}/approve", headers=auth("admin")).status_code == 200
        lease = api.post(
            "/api/v1/deploy/lease/acquire", headers=auth(), json={"agent_id": "alice", "release_id": rid}
        ).json()["data"]
        target, actor = f"/api/v1/deploy/lease/{lease['lease_id']}/release", "alice"
    audit = records(settings, "audit")
    result = api.post(target, headers=auth(actor), json={"service_plan_digest": "sha256:" + "c" * 64})
    assert result.status_code == 422 and records(settings, "audit") == audit
    with sqlite3.connect(settings.data_dir / "deploy.sqlite") as db:
        if action == "approve":
            assert db.execute("SELECT approved FROM releases WHERE id=?", (rid,)).fetchone()[0] == 0
        else:
            assert db.execute("SELECT id FROM lease").fetchone()[0] == lease["lease_id"]


@pytest.mark.parametrize("action", ["approve", "acquire"])
def test_unsupported_stored_release_cannot_advance_approval_or_lease(settings, action):
    api = TestClient(create_app("deploy-gateway", settings))
    rid = api.post("/api/v1/releases", headers=auth(), json=MANIFEST).json()["data"]["release_id"]
    with sqlite3.connect(settings.data_dir / "deploy.sqlite") as db:
        body = {**MANIFEST, "service_plan_digest": "sha256:" + "c" * 64}
        db.execute(
            "UPDATE releases SET manifest=?,approved=? WHERE id=?",
            (json.dumps(body), int(action == "acquire"), rid),
        )
    audit = records(settings, "audit")
    if action == "approve":
        result = api.post(f"/api/v1/releases/{rid}/approve", headers=auth("admin"))
    else:
        result = api.post(
            "/api/v1/deploy/lease/acquire", headers=auth(), json={"agent_id": "alice", "release_id": rid}
        )
    assert result.status_code == 409 and result.json()["error"]["code"] == "RELEASE_CONTRACT_UNSUPPORTED"
    assert records(settings, "audit") == audit
    with sqlite3.connect(settings.data_dir / "deploy.sqlite") as db:
        assert db.execute("SELECT id FROM lease").fetchone()[0] is None
        assert db.execute("SELECT approved FROM releases WHERE id=?", (rid,)).fetchone()[0] == int(
            action == "acquire"
        )


@pytest.mark.parametrize("body", [None, {}])
def test_null_and_empty_action_bodies_preserve_current_workflow(settings, body):
    api = TestClient(create_app("deploy-gateway", settings))
    rid = api.post("/api/v1/releases", headers=auth(), json=MANIFEST).json()["data"]["release_id"]
    headers = {**auth("admin"), "Content-Type": "application/json"}
    assert (
        api.post(f"/api/v1/releases/{rid}/approve", headers=headers, content=json.dumps(body)).status_code
        == 200
    )
    lease = api.post(
        "/api/v1/deploy/lease/acquire", headers=auth(), json={"agent_id": "alice", "release_id": rid}
    ).json()["data"]
    headers = {**auth(), "Content-Type": "application/json"}
    assert (
        api.post(
            f"/api/v1/deploy/lease/{lease['lease_id']}/release", headers=headers, content=json.dumps(body)
        ).status_code
        == 200
    )
