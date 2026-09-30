import subprocess
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
