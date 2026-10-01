import os
import subprocess
import sys
from pathlib import Path

import pytest

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
