import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from infra.devagent import stage_receiver
from xeonolab import deployment_staging


def test_qdrant_runtime_config_is_transferred_as_private_files(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        deployment_staging.subprocess,
        "check_output",
        lambda *args, **kwargs: "/var/tmp/xeonolab-qdrant-config.A1b2C3d4E5\n",
    )
    monkeypatch.setattr(deployment_staging.subprocess, "run", lambda args, **kwargs: calls.append(args))
    environment = tmp_path / "xelibrary.env"
    api_key = tmp_path / "qdrant-api-key"
    environment.write_text("QDRANT_URL=https://qdrant.example\n")
    api_key.write_text("must-not-appear-in-command-arguments")

    deployment_staging.stage_qdrant_runtime_config(
        "xenon@192.168.0.104", "/home/xenon/.ssh/release", environment, api_key
    )

    assert calls[0] == [
        "scp",
        "-i",
        "/home/xenon/.ssh/release",
        str(environment),
        "xenon@192.168.0.104:/var/tmp/xeonolab-qdrant-config.A1b2C3d4E5/xelibrary.env",
    ]
    assert calls[1][0] == "scp" and calls[1][-1].endswith("/qdrant-api-key")
    install = calls[2][-1]
    assert "install -m 640 -o root -g xeonolab" in install
    assert "/etc/xeonolab/qdrant-api-key" in install
    assert "/etc/xeonolab/xelibrary.env" in install
    assert "must-not-appear-in-command-arguments" not in repr(calls)
    assert calls[3][-1].endswith("rmdir /var/tmp/xeonolab-qdrant-config.A1b2C3d4E5")


def test_qdrant_runtime_config_rejects_unexpected_remote_path(monkeypatch, tmp_path):
    monkeypatch.setattr(
        deployment_staging.subprocess,
        "check_output",
        lambda *args, **kwargs: "/tmp/untrusted;touch\n",
    )
    monkeypatch.setattr(
        deployment_staging.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("unexpected path must not be used in a command"),
    )

    with pytest.raises(RuntimeError, match="staging path was invalid"):
        deployment_staging.stage_qdrant_runtime_config(
            "xenon@192.168.0.104", "identity", Path("xelibrary.env"), Path("key")
        )


def test_qdrant_runtime_config_cleans_remote_directory_after_transfer_failure(monkeypatch):
    calls = []
    monkeypatch.setattr(
        deployment_staging.subprocess,
        "check_output",
        lambda *args, **kwargs: "/var/tmp/xeonolab-qdrant-config.A1b2C3d4E5\n",
    )

    def run(args, **kwargs):
        calls.append(args)
        if args[0] == "scp":
            raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(deployment_staging.subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        deployment_staging.stage_qdrant_runtime_config(
            "xenon@192.168.0.104", "identity", Path("xelibrary.env"), Path("key")
        )
    assert calls[-1][-1].endswith("rmdir /var/tmp/xeonolab-qdrant-config.A1b2C3d4E5")


def test_dev_agent_claim_config_is_installed_root_owned_without_command_secrets(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        deployment_staging.subprocess,
        "check_output",
        lambda *args, **kwargs: "/var/tmp/xeonolab-forgejo-claim.A1b2C3d4E5\n",
    )
    monkeypatch.setattr(deployment_staging.subprocess, "run", lambda args, **kwargs: calls.append(args))
    environment = tmp_path / "dev-agent.env"
    token = tmp_path / "forgejo-claim-token"
    users = tmp_path / "forgejo-agent-users.json"
    environment.write_text("SERVICE=dev-agent\n")
    token.write_text("fixture-only-secret")
    users.write_text('{"subject-123":"general-agent"}')
    token.chmod(0o600)
    users.chmod(0o600)

    deployment_staging.stage_dev_agent_claim_config(
        "xenon@192.168.0.111", "/home/xenon/.ssh/release", environment, token, users
    )

    assert [call[0] for call in calls] == ["scp", "scp", "scp", "ssh", "ssh"]
    assert calls[0][-1].endswith("/dev-agent.env")
    assert calls[1][-1].endswith("/forgejo-claim-token")
    assert calls[2][-1].endswith("/forgejo-agent-users.json")
    install = calls[3][-1]
    assert "install -d -m 750 -o root -g xeonolab /etc/xeonolab" in install
    assert install.count("install -m 640 -o root -g xeonolab") == 3
    assert "/etc/xeonolab/dev-agent.env" in install
    assert "/etc/xeonolab/forgejo-claim-token" in install
    assert "/etc/xeonolab/forgejo-agent-users.json" in install
    assert "fixture-only-secret" not in repr(calls)
    assert calls[4][-1].endswith("rmdir /var/tmp/xeonolab-forgejo-claim.A1b2C3d4E5")


def test_dev_agent_claim_config_rejects_missing_inputs_before_remote_staging(monkeypatch, tmp_path):
    monkeypatch.setattr(
        deployment_staging.subprocess,
        "check_output",
        lambda *args, **kwargs: pytest.fail("missing inputs must fail before SSH"),
    )
    token = tmp_path / "missing-token"
    users = tmp_path / "missing-users.json"

    with pytest.raises(ValueError, match="private Forgejo claim input"):
        deployment_staging.stage_dev_agent_claim_config(
            "xenon@192.168.0.111", "identity", Path("env"), token, users
        )


def test_release_stager_preflights_claim_inputs_before_creating_archive():
    source = Path(__file__).resolve().parents[1] / "scripts/stage_release.py"
    text = source.read_text()

    assert text.index("validate_forgejo_claim_inputs(claim_token, agent_users)") < text.index(
        'command("git", "archive"'
    )
    assert "stage_dev_agent_claim_config(" in text


def test_prepare_deployment_fails_before_payload_generation_without_claim_inputs(tmp_path):
    repository = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(repository)}
    result = subprocess.run(
        [sys.executable, str(repository / "scripts/prepare_deployment.py")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "required private Forgejo claim input is unavailable" in result.stderr
    assert not (tmp_path / "runtime").exists()
    assert "fixture-only-secret" not in result.stdout + result.stderr


def test_release_report_matches_current_validation_and_openapi_change():
    source = Path(__file__).resolve().parents[1] / "scripts/prepare_deployment.py"
    text = source.read_text()

    assert "uv run pytest 279 passed" in text
    assert "OpenAPI regenerated with DevAgent claim side effects" in text
    assert "safe comment cleanup" in text
    assert "nullable Forgejo assignees" in text
    assert "uv run pytest 276 passed" not in text
    assert "uv run pytest 250 passed" not in text
    assert "234 backend tests passed" not in text
    assert "OpenAPI export unchanged" not in text


def _publication_probe_fixture(tmp_path, monkeypatch):
    release_root = tmp_path / "releases"
    commit = "a" * 40
    release = release_root / commit
    release.mkdir(parents=True)
    platform = tmp_path / "platform"
    platform.mkdir()
    (platform / "current").symlink_to(release)
    activation = tmp_path / "activation.json"
    activation.write_text(json.dumps({"address": "192.168.0.106", "ports": [8102, 8105]}))
    db_path = tmp_path / "deploy.sqlite"
    with sqlite3.connect(db_path) as db:
        db.executescript("""
            CREATE TABLE lease(singleton INTEGER PRIMARY KEY, id TEXT, holder TEXT,
                release_id TEXT, epoch INTEGER NOT NULL, expires REAL);
            CREATE TABLE deployments(id TEXT, state TEXT);
            INSERT INTO lease VALUES(1,'lease-123','codex-maintainer','rel-abc',7,1791000000.0);
            INSERT INTO deployments VALUES('done','completed');
            INSERT INTO deployments VALUES('rolled','rolled_back');
            INSERT INTO deployments VALUES('cancelled','cancelled');
            INSERT INTO deployments VALUES('pending','queued');
            INSERT INTO deployments VALUES('unknown',NULL);
        """)
    monkeypatch.setattr(stage_receiver, "ROOT", release_root)
    monkeypatch.setattr(stage_receiver, "PLATFORM", platform)
    monkeypatch.setattr(stage_receiver, "ACTIVATION", activation)
    monkeypatch.setattr(stage_receiver, "DEPLOY_DATABASE", db_path)
    monkeypatch.setattr(stage_receiver, "_gateway_runtime_cwd", lambda pid: release)
    calls = []

    class Response:
        status = 200

        def read(self, size):
            assert size == 4097
            return b'{"status":"ready","service":"deploy-gateway"}'

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class Opener:
        def open(self, url, timeout):
            calls.append((url, timeout))
            return Response()

    monkeypatch.setattr(stage_receiver.urllib.request, "build_opener", lambda *args: Opener())
    return release, calls


def test_publication_recovery_is_read_only_and_proves_runtime_lease_and_queue(monkeypatch, tmp_path):
    release, calls = _publication_probe_fixture(tmp_path, monkeypatch)
    commands = []

    def systemctl(args, **kwargs):
        commands.append(args)
        assert kwargs["check"] is True and kwargs["timeout"] == 10
        return subprocess.CompletedProcess(args, 0, "4242\n", "")

    monkeypatch.setattr(stage_receiver.subprocess, "run", systemctl)
    result = stage_receiver.publication_recovery()

    assert result == {
        "gateway_commit": release.name,
        "runtime_commit": release.name,
        "ready": True,
        "lease": {
            "id": "lease-123",
            "holder": "codex-maintainer",
            "release_id": "rel-abc",
            "epoch": 7,
            "expires": 1791000000.0,
        },
        "unresolved_deployments": 2,
    }
    assert commands == [[
        "/usr/bin/systemctl", "show", "xeonolab@deploy-gateway.service",
        "--property=MainPID", "--value", "--no-pager",
    ]]
    assert calls == [("http://192.168.0.106:8103/health/ready", 5)]
    with sqlite3.connect(f"file:{stage_receiver.DEPLOY_DATABASE}?mode=ro", uri=True) as db:
        assert db.execute("SELECT COUNT(*) FROM deployments").fetchone() == (5,)


@pytest.mark.parametrize("pid_output", ["", "0", "not-a-pid", "1\nextra"])
def test_publication_recovery_rejects_unavailable_or_malformed_main_pid(monkeypatch, tmp_path, pid_output):
    _publication_probe_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        stage_receiver.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, pid_output, ""),
    )
    with pytest.raises(ValueError, match="PID unavailable"):
        stage_receiver.publication_recovery()


def test_publication_recovery_rejects_gateway_process_from_another_release(monkeypatch, tmp_path):
    release, calls = _publication_probe_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        stage_receiver.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "4242\n", ""),
    )
    monkeypatch.setattr(stage_receiver, "_gateway_runtime_cwd", lambda _pid: release.parent)
    with pytest.raises(ValueError, match="not running the current immutable release"):
        stage_receiver.publication_recovery()
    assert calls == []


def test_publication_recovery_rejects_incomplete_lease_schema(monkeypatch, tmp_path):
    _publication_probe_fixture(tmp_path, monkeypatch)
    with sqlite3.connect(stage_receiver.DEPLOY_DATABASE) as db:
        db.execute("ALTER TABLE lease RENAME TO lease_old")
        db.execute("CREATE TABLE lease(singleton INTEGER PRIMARY KEY, id TEXT, epoch INTEGER)")
        db.execute("INSERT INTO lease VALUES(1,'lease-123',7)")
    with pytest.raises(ValueError, match="lease schema is incomplete"):
        stage_receiver.publication_recovery()


def test_publication_recovery_rejects_http_failure_and_wrong_activation(monkeypatch, tmp_path):
    _publication_probe_fixture(tmp_path, monkeypatch)

    class BadResponse:
        status = 503

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class BadOpener:
        def open(self, _url, timeout):
            return BadResponse()

    monkeypatch.setattr(stage_receiver.urllib.request, "build_opener", lambda *args: BadOpener())
    monkeypatch.setattr(
        stage_receiver.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "4242\n", ""),
    )
    with pytest.raises(RuntimeError, match="readiness failed"):
        stage_receiver.publication_recovery()

    stage_receiver.ACTIVATION.write_text(json.dumps({"address": "192.168.0.104"}))
    with pytest.raises(ValueError, match="gateway target required"):
        stage_receiver.publication_recovery()


def test_publication_recovery_rejects_wrong_or_oversized_ready_payload(monkeypatch, tmp_path):
    _publication_probe_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(
        stage_receiver.subprocess,
        "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 0, "4242\n", ""),
    )

    class Response:
        status = 200

        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, size):
            return self.body[:size]

    class Opener:
        def __init__(self, body):
            self.body = body

        def open(self, _url, timeout):
            return Response(self.body)

    for body, message in (
        (b'{"status":"ready","service":"desktop-web"}', "malformed"),
        (b" " * 4097, "oversized"),
    ):
        monkeypatch.setattr(stage_receiver.urllib.request, "build_opener", lambda *args, _body=body: Opener(_body))
        with pytest.raises((ValueError, RuntimeError), match=message):
            stage_receiver.publication_recovery()


def test_publication_recovery_forced_command_accepts_only_exact_fixed_command(monkeypatch, capsys):
    expected = {
        "gateway_commit": "a" * 40,
        "runtime_commit": "a" * 40,
        "ready": True,
        "lease": {"id": None, "holder": None, "release_id": None, "epoch": 0, "expires": None},
        "unresolved_deployments": 0,
    }
    monkeypatch.setattr(stage_receiver, "publication_recovery", lambda: expected)
    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "devagent-stage publication-recovery")
    stage_receiver.main()
    assert json.loads(capsys.readouterr().out) == expected

    monkeypatch.setenv("SSH_ORIGINAL_COMMAND", "devagent-stage publication-recovery 101")
    with pytest.raises(ValueError, match="fixed staging protocol"):
        stage_receiver.main()
