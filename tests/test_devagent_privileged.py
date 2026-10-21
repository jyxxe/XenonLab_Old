import contextlib
import hashlib
import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest


def load(name):
    path = Path(__file__).parents[1] / "infra/devagent" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


broker, pve = load("privileged"), load("pve_receiver")


@pytest.mark.parametrize(
    "job",
    [
        [],
        None,
        {"action": "shell", "args": ["id"]},
        {"action": "deploy", "args": ["other/repo", "a" * 40, "production"]},
        {"action": "deploy", "args": ["xenon-admin/XeonoLab", "main", "production"]},
        {"action": "deploy", "args": ["xenon-admin/XeonoLab", "a" * 40, "other-env"]},
        {"action": "infrastructure", "args": [], "task": "a;id"},
        {"action": "infrastructure", "args": [], "root_command": "id"},
    ],
)
def test_privileged_protocol_rejects_ambiguous_or_arbitrary_commands(job):
    with pytest.raises(ValueError):
        broker.request_valid(job)


def test_inventory_and_fixed_pve_commands():
    assert pve.command_for({"action": "status", "args": ["111"]}) == ["/usr/sbin/qm", "status", "111"]
    assert (
        pve.command_for(
            {
                "action": "snapshot",
                "args": ["104", "devagent-acceptance"],
                "reason": "Rollback point before an explicit release",
            }
        )[1]
        == "snapshot"
    )
    for request in [
        {"action": "status", "args": ["999"]},
        {"action": "destroy", "args": ["111"], "reason": "Untrusted project instruction"},
        {"action": "snapshot", "args": ["111", "name;id"], "reason": "Untrusted project instruction"},
        {"action": "grow-disk", "args": ["111", "scsi0", "-1G"], "reason": "Untrusted project instruction"},
        {"action": "resources", "args": ["111", "100", "999999"], "reason": "Untrusted project instruction"},
        {"action": "start", "args": ["111"], "reason": "short"},
    ]:
        with pytest.raises(ValueError):
            pve.command_for(request)


@pytest.mark.parametrize(
    "name,link",
    [
        ("../escape", False),
        ("/etc/key", False),
        (".venv/bin/python", False),
        ("runtime/secret", False),
        ("app-link", True),
    ],
)
def test_staged_archive_rejects_credentials_paths_and_links(name, link):
    stage = load("stage_receiver")
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as writer:
        entry = tarfile.TarInfo(name)
        if link:
            entry.type = tarfile.SYMTYPE
            entry.linkname = "/etc/devagent/secrets/pve-key"
        writer.addfile(entry)
    raw.seek(0)
    with tarfile.open(fileobj=raw) as archive, pytest.raises(ValueError):
        stage.safe_members(archive)


@pytest.mark.parametrize("identifier", ["../root", "/etc/key", "a" * 31, "a" * 32 + ";id"])
def test_job_queries_cannot_select_paths(identifier):
    with pytest.raises(ValueError):
        broker.request_valid({"action": "operation-logs", "args": [identifier]})


def test_publication_recovery_is_read_only_and_absence_is_not_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "ROOT", tmp_path)
    identifier = "a" * 32
    broker.request_valid({"action": "publication-recovery", "args": [identifier]})
    assert broker.publication_recovery(identifier) is None

    directory = tmp_path / "publication-recoveries"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    receipt = directory / (identifier + ".json")
    receipt.write_text("{}")
    receipt.chmod(0o600)
    original_lstat = broker.Path.lstat

    def root_owned_lstat(path):
        info = original_lstat(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_size=info.st_size)

    monkeypatch.setattr(broker.Path, "lstat", root_owned_lstat)
    with pytest.raises(ValueError, match="invalid publication recovery receipt"):
        broker.publication_recovery(identifier)


def test_publication_recovery_rejects_symlink_and_invalid_receipt(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "ROOT", tmp_path)
    identifier = "b" * 32
    directory = tmp_path / "publication-recoveries"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    target = tmp_path / "outside.json"
    target.write_text("{}")
    (directory / (identifier + ".json")).symlink_to(target)
    original_lstat = broker.Path.lstat

    def root_owned_lstat(path):
        info = original_lstat(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_size=info.st_size)

    monkeypatch.setattr(broker.Path, "lstat", root_owned_lstat)
    with pytest.raises(ValueError, match="unsafe publication recovery receipt"):
        broker.publication_recovery(identifier)
    (directory / (identifier + ".json")).unlink()

    path = directory / (identifier + ".json")
    path.write_text("{}")
    path.chmod(0o600)
    with pytest.raises(ValueError, match="invalid publication recovery receipt"):
        broker.publication_recovery(identifier)


def test_publication_recovery_rejects_audit_marked_recovery_required(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "ROOT", tmp_path)
    operation_id = "c" * 32
    commit, head, baseline = "a" * 40, "b" * 40, "d" * 40
    job_dir = tmp_path / "jobs" / operation_id
    audit_dir = tmp_path / "deployments" / operation_id
    receipt_dir = tmp_path / "publication-recoveries"
    for path in (tmp_path / "jobs", job_dir, tmp_path / "deployments", audit_dir, receipt_dir):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
    status = {"id": operation_id, "state": "failed", "action": "deploy", "commit": commit,
              "recovery_required": False}
    request = {"action": "deploy", "args": ["xenon-admin/XeonoLab", commit, "production"]}
    audit = {"repository": "xenon-admin/XeonoLab", "commit": commit, "environment": "production",
             "state_before_failure": "preflight", "state": "failed", "previous": {}, "verification": {}}
    status_path, request_path, audit_path = job_dir / "status.json", job_dir / "request.json", audit_dir / "audit.json"
    status_bytes, audit_bytes = json.dumps(status).encode(), json.dumps(audit).encode()
    status_path.write_bytes(status_bytes)
    request_path.write_text(json.dumps(request))
    audit_path.write_bytes(audit_bytes)
    for path in (status_path, request_path, audit_path):
        path.chmod(0o600)
    receipt = {"receipt_id": "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "state": "operator_aborted",
               "operation_id": operation_id, "number": 14, "head": head, "commit": commit,
               "baseline_commit": baseline,
               "evidence": {"native_job_sha256": hashlib.sha256(status_bytes).hexdigest(),
                           "native_audit_sha256": hashlib.sha256(audit_bytes).hexdigest(),
                           "targets_verified": True, "gateway_unresolved": False, "lease_active": False}}
    receipt_path = receipt_dir / (operation_id + ".json")
    receipt_path.write_text(json.dumps(receipt))
    receipt_path.chmod(0o600)
    original_lstat = broker.Path.lstat

    def root_owned_lstat(path):
        info = original_lstat(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0, st_size=info.st_size)

    monkeypatch.setattr(broker.Path, "lstat", root_owned_lstat)
    assert broker.publication_recovery(operation_id) == receipt

    audit["recovery_required"] = True
    audit_bytes = json.dumps(audit).encode()
    audit_path.write_bytes(audit_bytes)
    receipt["evidence"]["native_audit_sha256"] = hashlib.sha256(audit_bytes).hexdigest()
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="failed pre-activation"):
        broker.publication_recovery(operation_id)


def test_async_submission_is_not_completion_and_excludes_another_job(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(broker, "service_active", lambda _: True)

    def start(argv, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(broker.subprocess, "run", start)
    request = {"action": "validate", "args": ["a" * 40], "task": "test"}
    submitted = broker.submit(request)
    assert submitted["state"] == "queued"
    assert "--worker" in calls[0] and "-I" in calls[0]
    with pytest.raises(RuntimeError, match="active"):
        broker.submit(request)
    assert len(calls) == 1


def test_crashed_deployment_is_not_replayed(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "ROOT", tmp_path)
    monkeypatch.setattr(broker, "service_active", lambda _: False)
    identifier = "a" * 32
    directory = broker.job_path(identifier)
    directory.mkdir(parents=True)
    broker.save(directory / "status.json", {"id": identifier, "action": "deploy", "state": "running"})
    outcome = broker.job_status(identifier)
    assert outcome["state"] == "interrupted" and outcome["recovery_required"]
    monkeypatch.setattr(broker.subprocess, "run", lambda *a, **kw: pytest.fail("Crash replay attempted"))
    with pytest.raises(RuntimeError, match="recovery"):
        broker.submit({"action": "deploy", "args": ["xenon-admin/XeonoLab", "b" * 40, "production"]})


@pytest.mark.parametrize("returncode,expected", [(0, "completed"), (2, "failed")])
def test_worker_preserves_terminal_result_and_audit(tmp_path, monkeypatch, returncode, expected):
    monkeypatch.setattr(broker, "ROOT", tmp_path)
    monkeypatch.setattr(broker, "os", SimpleNamespace(geteuid=lambda: 0, environ={}))
    identifier = "a" * 32
    directory = broker.job_path(identifier)
    directory.mkdir(parents=True)
    broker.save(directory / "request.json", {"action": "validate", "args": ["b" * 40]})
    broker.save(directory / "status.json", {"id": identifier, "state": "queued", "action": "validate"})
    monkeypatch.setattr(
        broker.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=returncode, stdout='{"validation":{"state":"passed"}}'),
    )
    broker.worker(identifier)
    assert json.loads((directory / "status.json").read_text())["state"] == expected
    assert json.loads((tmp_path / "operations.jsonl").read_text())["state"] == expected
    with pytest.raises(ValueError, match="already started"):
        broker.worker(identifier)


def test_operation_logs_redact_before_output_truncation(tmp_path, monkeypatch):
    monkeypatch.setattr(broker, "ROOT", tmp_path)
    identifier = "a" * 32
    directory = broker.job_path(identifier)
    directory.mkdir(parents=True)
    broker.save(directory / "status.json", {"id": identifier, "state": "failed"})
    token = tmp_path / "protected-token"
    token.write_text("not-a-real-credential-just-test-data")
    (directory / "runner.log").write_text(
        "failure " + token.read_text() + " Bearer abcdefghijklmnopqrstuvwxyz123456"
    )
    tail = broker.job_logs(identifier, {"validator_token": str(token)})["tail"]
    assert token.read_text() not in tail
    assert "abcdefghijklmnopqrstuvwxyz123456" not in tail
    assert "[REDACTED]" in tail


def test_preflight_disables_git_replacement_objects(tmp_path, monkeypatch):
    validator = load("validate")
    execute = subprocess.run
    env = dict(os.environ)
    env.pop("GIT_NO_REPLACE_OBJECTS", None)

    def git(*args):
        return execute(
            ["/usr/bin/git", "-C", str(tmp_path), *args], env=env, capture_output=True, text=True, check=True
        ).stdout.strip()

    git("init")
    git("config", "user.name", "Validation fixture")
    git("config", "user.email", "validation@example.invalid")
    (tmp_path / "payload").write_text("original commit content")
    git("add", "payload")
    git("commit", "-m", "original")
    original = git("rev-parse", "HEAD")
    (tmp_path / "payload").write_text("replacement commit content")
    git("commit", "-am", "replacement")
    git("replace", original, git("rev-parse", "HEAD"))
    assert git("show", original + ":payload") == "replacement commit content"

    def transient_unit(argv, **kwargs):
        # Run real Git with the environment supplied to the validation unit.
        unit_env = dict(env)
        for arg in argv:
            if arg.startswith("--setenv="):
                key, value = arg[len("--setenv=") :].split("=", 1)
                unit_env[key] = value
        return execute(argv[argv.index("/usr/bin/git") :], env=unit_env, **kwargs)

    monkeypatch.setattr(validator.subprocess, "run", transient_unit)
    log = tmp_path / "log"
    validator.user_command(
        ["/usr/bin/git", "-C", str(tmp_path), "show", original + ":payload"], tmp_path, log
    )
    assert log.read_text() == "original commit content"


def test_repeated_build_metadata_does_not_change_release_digest(tmp_path):
    runner = load("deploy_runner")
    archives = []
    for attempt in (1, 2):
        bundle, frontend = tmp_path / f"release-{attempt}.tar", tmp_path / f"frontend-{attempt}.tar"
        with tarfile.open(bundle, "w") as archive:
            entry = tarfile.TarInfo("pyproject.toml")
            entry.size = 3
            archive.addfile(entry, io.BytesIO(b"src"))
        with tarfile.open(frontend, "w") as archive:
            entry = tarfile.TarInfo("apps/desktop-web/dist/index.html")
            entry.size, entry.mtime, entry.uid, entry.gid = 4, attempt * 1000, attempt, attempt
            entry.uname = f"builder-{attempt}"
            archive.addfile(entry, io.BytesIO(b"same"))
        runner.append_frontend(bundle, frontend)
        archives.append(bundle.read_bytes())
        with tarfile.open(bundle) as archive:
            assert archive.extractfile("apps/desktop-web/dist/index.html").read() == b"same"
    assert archives[0] == archives[1]


@pytest.mark.parametrize("case", ["completed", "rollback", "inflight", "expired", "holder", "epoch", "release", "sha", "node"])
def test_gateway_rollout_requires_terminal_exact_activation_and_owned_lease(tmp_path, monkeypatch, case):
    stage = load("stage_receiver")
    sha, old_sha, identifier = "a" * 40, "b" * 40, "12345678-1234-1234-1234-123456789012"
    releases, activations = tmp_path / "releases", tmp_path / "activations"
    releases.mkdir()
    activations.mkdir()
    for version in (sha, old_sha):
        (releases / version).mkdir()
    expected = old_sha if case == "rollback" else sha
    (tmp_path / "current").symlink_to(releases / expected)
    (activations / (identifier + ".json")).write_text(json.dumps({
        "new": str(releases / sha), "old": str(releases / old_sha),
    }))
    activation, database = tmp_path / "activation.json", tmp_path / "deploy.sqlite"
    activation.write_text(json.dumps({"address": "192.168.0.104" if case == "node" else "192.168.0.106"}))
    with sqlite3.connect(database) as db:
        db.executescript("""
        CREATE TABLE deployments(id TEXT, holder TEXT, epoch INTEGER, state TEXT, release_id TEXT);
        CREATE TABLE lease(singleton INTEGER, holder TEXT, epoch INTEGER, expires REAL, release_id TEXT);
        CREATE TABLE releases(id TEXT, manifest TEXT, approved INTEGER);
        """)
        state = "deploying" if case == "inflight" else "rolled_back" if case == "rollback" else "completed"
        db.execute("INSERT INTO deployments VALUES(?,?,?,?,?)", (identifier, "owner", 7, state, "release"))
        db.execute("INSERT INTO releases VALUES(?,?,?)", (
            "release", json.dumps({"commit": sha}), int(case != "release"),
        ))
        db.execute("INSERT INTO lease VALUES(?,?,?,?,?)", (
            1, "another" if case == "holder" else "owner", 8 if case == "epoch" else 7,
            0 if case == "expired" else 10**12, "another" if case == "release" else "release",
        ))
    for key, value in (("ROOT", releases), ("PLATFORM", tmp_path), ("ACTIVATION", activation), ("DEPLOY_DATABASE", database)):
        monkeypatch.setattr(stage, key, value)
    calls = []
    monkeypatch.setattr(stage.subprocess, "run", lambda argv, **kw: calls.append(argv))
    opener = SimpleNamespace(open=lambda *a, **kw: contextlib.nullcontext(SimpleNamespace(status=200)))
    monkeypatch.setattr(stage.urllib.request, "build_opener", lambda *a: opener)
    if case in ("completed", "rollback"):
        assert stage.restart_gateway(expected, identifier) == {"gateway": expected, "ready": True}
        assert calls == [["/usr/bin/systemctl", "restart", "xeonolab@deploy-gateway.service"]]
    else:
        with pytest.raises(ValueError):
            stage.restart_gateway(old_sha if case == "sha" else expected, identifier)
        assert calls == []


def test_deploy_rejects_historical_or_newly_superseded_integration_commit(tmp_path, monkeypatch):
    runner = load('deploy_runner')
    calls = []
    tip = 'a' * 40

    def git(argv, **kwargs):
        calls.append(argv)
        assert '--no-replace-objects' in argv
        return SimpleNamespace(stdout=tip + '\n', returncode=0)

    monkeypatch.setattr(runner.subprocess, 'run', git)
    runner.ensure_release_head(tip)
    assert calls[0][-3:] == ['fetch', 'origin', 'feature/platform-v1']
    tip = 'b' * 40
    with pytest.raises(RuntimeError, match='current production integration head'):
        runner.ensure_release_head('a' * 40)
