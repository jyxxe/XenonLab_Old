import base64
import importlib.util
import json
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location(
    "publication_recovery", Path(__file__).parents[1] / "infra/devagent/publication_recovery.py"
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)

FAILED_ID, BASELINE_ID = "a" * 32, "b" * 32
FAILED_SHA, BASELINE_SHA, HEAD = "c" * 40, "d" * 40, "e" * 40
RELEASE_ID = "11111111-1111-4111-8111-111111111111"
LEASE_ID = "22222222-2222-4222-8222-222222222222"
HOLDER = "33333333-3333-4333-8333-333333333333"


@pytest.fixture
def request_value():
    return {"number": 147, "head": HEAD, "commit": FAILED_SHA, "operation_id": FAILED_ID,
            "baseline_commit": BASELINE_SHA, "baseline_operation_id": BASELINE_ID,
            "reason": "Checked preflight failure before activation"}


@pytest.mark.parametrize("change", [
    {"number": True}, {"number": 0}, {"head": "../../file"}, {"operation_id": "A" * 32},
    {"baseline_commit": FAILED_SHA}, {"baseline_operation_id": FAILED_ID}, {"reason": "\nsecret"},
    {"extra": "shell"},
])
def test_operator_request_rejects_ambiguous_or_unbounded_inputs(request_value, change):
    request_value.update(change)
    with pytest.raises(ValueError):
        recovery.validate_request(request_value)


def test_receipt_atomic_write_is_private_and_never_overwrites(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "owned_directory", lambda _: None)
    path = tmp_path / "receipt.json"
    recovery.atomic_json(path, {"original": True})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        recovery.atomic_json(path, {"original": False})
    assert json.loads(path.read_text()) == {"original": True}
    assert not list(tmp_path.glob(".*.tmp"))


def test_operator_caller_is_checked_before_native_access(monkeypatch, capsys):
    monkeypatch.setattr(recovery.sys, "argv", ["recovery", "--abort-preflight"])
    monkeypatch.setattr(recovery.os, "geteuid", lambda: 0)
    monkeypatch.setenv("SUDO_USER", "devagent")
    with pytest.raises(ValueError, match="operator root"):
        recovery.main()
    monkeypatch.setattr(recovery.sys, "argv", ["recovery"])
    monkeypatch.setattr(recovery, "load_runner", lambda: pytest.fail("plan imported native runner"))
    recovery.main()
    assert json.loads(capsys.readouterr().out)["network"] is False


@pytest.fixture
def native_baseline(tmp_path, monkeypatch, request_value):
    monkeypatch.setattr(recovery, "ROOT", tmp_path)
    monkeypatch.setattr(recovery, "owned_directory", lambda _: None)
    failed = {"id": FAILED_ID, "action": "deploy", "state": "failed", "commit": FAILED_SHA,
              "recovery_required": False, "created_at": 30}
    audit = {"state": "failed", "state_before_failure": "preflight", "previous": {}, "verification": {}}
    baseline = {"id": BASELINE_ID, "action": "deploy", "state": "completed", "result": "completed",
                "commit": BASELINE_SHA, "finished_at": 20,
                "verification": {node: {"expected": BASELINE_SHA, "actual": BASELINE_SHA, "ok": True}
                                 for node in recovery.TARGETS}}
    baseline["verification"]["gateway"] = {"ok": True}
    baseline_audit = {"state": "completed", "release_id": RELEASE_ID}
    jobs = {FAILED_ID: (failed, audit, "1" * 64, "2" * 64),
            BASELINE_ID: (baseline, baseline_audit, "3" * 64, "4" * 64)}
    monkeypatch.setattr(recovery, "native_job", lambda identifier: jobs[identifier])
    for identifier, values in jobs.items():
        directory = tmp_path / "jobs" / identifier
        directory.mkdir(parents=True)
        (directory / "status.json").write_text(json.dumps(values[0]))
    monkeypatch.setattr(recovery, "read_json", lambda path: (json.loads(path.read_text()), "5" * 64))
    monkeypatch.setattr(recovery.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=3))
    return failed, audit, baseline, baseline_audit


@pytest.mark.parametrize("change", [
    {"state_before_failure": "staging"}, {"previous": {"101": BASELINE_SHA}},
    {"lease": None}, {"artifact": {}}, {"deployment": {}}, {"recovery_required": True},
])
def test_abort_refuses_any_activation_or_recovery_evidence(request_value, native_baseline, change):
    native_baseline[1].update(change)
    with pytest.raises(ValueError, match="pre-activation"):
        recovery.check_native(request_value)


def test_abort_requires_complete_successful_baseline(request_value, native_baseline):
    native_baseline[2]["verification"].pop("gateway")
    with pytest.raises(ValueError, match="all native baseline"):
        recovery.check_native(request_value)


def test_other_active_job_blocks_abort(request_value, native_baseline, tmp_path):
    directory = tmp_path / "jobs" / ("f" * 32)
    directory.mkdir()
    (directory / "status.json").write_text(json.dumps({"id": "f" * 32, "state": "running", "action": "validate"}))
    with pytest.raises(ValueError, match="active or unresolved"):
        recovery.check_native(request_value)


def test_receipt_tuple_is_bound_to_actual_forgejo_merge(request_value, monkeypatch):
    pr = {"merged": True, "state": "closed", "head": {"sha": HEAD}, "merge_commit_sha": FAILED_SHA,
          "base": {"ref": "feature/platform-v1", "repo": {"full_name": recovery.REPOSITORY}}}
    monkeypatch.setattr(recovery, "forgejo_get", lambda config, path, deadline:
                        pr if path.startswith("/pulls/") else {"commit": {"id": FAILED_SHA}})
    recovery.check_forgejo({}, request_value)
    pr["head"]["sha"] = BASELINE_SHA
    with pytest.raises(ValueError, match="tuple mismatch"):
        recovery.check_forgejo({}, request_value)


@pytest.mark.parametrize("url", ["http://192.168.0.101:9445", "https://user@192.168.0.101:9445",
                                  "https://example.com", "https://192.168.0.101:9445/?query=token"])
def test_forgejo_credentials_never_go_to_an_unexpected_origin(url):
    with pytest.raises(ValueError, match="fixed TLS Forgejo"):
        recovery.forgejo_get({"forgejo_url": url}, "/pulls/147", recovery.time.monotonic() + 10)


def test_gateway_io_is_bounded_and_refuses_expired_deadline():
    class Opener:
        def open(self, request, timeout):
            assert 0 < timeout <= 15
            return "bounded"

    opener = recovery.BoundedOpener(Opener(), recovery.time.monotonic() + 30)
    assert opener.open("request", timeout=650) == "bounded"
    opener.deadline = recovery.time.monotonic() - 1
    with pytest.raises(ValueError, match="deadline"):
        opener.open("request", timeout=650)


@pytest.fixture
def simulated_operator(tmp_path, monkeypatch):
    monkeypatch.setattr(recovery, "ROOT", tmp_path)
    monkeypatch.setattr(recovery, "owned_directory", lambda _: None)
    evidence = {"native_job_sha256": "1" * 64, "native_audit_sha256": "2" * 64}
    monkeypatch.setattr(recovery, "check_native", lambda *a: (evidence, {"release_id": RELEASE_ID}))
    monkeypatch.setattr(recovery, "check_forgejo", lambda *a: None)

    class Gateway:
        def __init__(self):
            self.lease = {"id": None, "holder": None, "release_id": None, "epoch": 7, "expires": None}
            self.calls = []
            self.unknown_acquire = False
            self.unknown_release = False
            self.opener = object()

        def token(self):
            payload = base64.urlsafe_b64encode(json.dumps({"sub": HOLDER}).encode()).decode().rstrip("=")
            return "header." + payload + ".signature"

        def api(self, method, path, body=None):
            self.calls.append((method, path))
            if path == "/api/v1/releases":
                return [{"id": RELEASE_ID, "approved": 1, "manifest": json.dumps({"commit": BASELINE_SHA})}]
            if method == "GET":
                return dict(self.lease)
            if path.endswith("/acquire"):
                self.lease.update(id=LEASE_ID, holder=HOLDER, release_id=RELEASE_ID, epoch=8,
                                  expires=recovery.time.time() + 300)
                if self.unknown_acquire:
                    raise TimeoutError("unknown outcome")
                return {"lease_id": LEASE_ID, "epoch": 8}
            if path.endswith("/release"):
                if self.unknown_release:
                    raise TimeoutError("unknown outcome")
                self.lease.update(id=None, holder=None, release_id=None, expires=None)
                return {"released": True}
            pytest.fail("unexpected API mutation")

    gateway = Gateway()
    live = {"current": BASELINE_SHA, "ready": True}
    proof = {"gateway_commit": BASELINE_SHA, "runtime_commit": BASELINE_SHA,
             "ready": True, "unresolved_deployments": 0}

    def ssh(config, node, command, deadline):
        if command == "devagent-stage status":
            return dict(live)
        assert node == "106" and command == "devagent-stage publication-recovery"
        return dict(proof, lease=dict(gateway.lease))

    monkeypatch.setattr(recovery, "stage_probe", ssh)
    return SimpleNamespace(Gateway=lambda _: gateway), gateway, live, proof


def test_operator_abort_preserves_failed_job_and_requires_released_lease(request_value, simulated_operator, tmp_path):
    runner, gateway, _, _ = simulated_operator
    receipt = recovery.abort(request_value, {}, runner)
    assert set(receipt) == {"receipt_id", "state", "operation_id", "number", "head", "commit",
                            "baseline_commit", "evidence"}
    assert receipt["state"] == "operator_aborted" and recovery.free_lease(gateway.lease)
    assert receipt["commit"] == FAILED_SHA and receipt["baseline_commit"] == BASELINE_SHA
    assert not (tmp_path / "jobs").exists()  # Never rewrites or creates a job/ledger.
    assert not any(path == "/api/v1/deployments" for _, path in gateway.calls)
    with pytest.raises(ValueError, match="already exists"):
        recovery.abort(request_value, {}, runner)


@pytest.mark.parametrize("failure", ["changed_target", "unresolved_gateway", "changed_runtime", "unknown_acquire", "unknown_release"])
def test_abort_failure_never_publishes_receipt(request_value, simulated_operator, tmp_path, failure):
    runner, gateway, live, proof = simulated_operator
    if failure == "changed_target":
        live["current"] = FAILED_SHA
    elif failure == "unresolved_gateway":
        proof["unresolved_deployments"] = 1
    elif failure == "changed_runtime":
        proof["runtime_commit"] = FAILED_SHA
    else:
        setattr(gateway, failure, True)
    with pytest.raises((ValueError, TimeoutError)):
        recovery.abort(request_value, {}, runner)
    assert not (tmp_path / "publication-recoveries" / (FAILED_ID + ".json")).exists()
    attempt = json.loads((tmp_path / "publication-recovery-attempts" / (FAILED_ID + ".json")).read_text())
    if failure in ("unknown_acquire", "unknown_release"):
        assert gateway.lease["id"] == LEASE_ID
        assert attempt["state"] in ("acquiring", "verified")
    else:
        assert recovery.free_lease(gateway.lease)
        assert attempt["state"] == "released_failed_checks"
