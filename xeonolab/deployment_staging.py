"""Coordinator-only transfer of protected runtime configuration."""

import re
import subprocess
from pathlib import Path

from .deploy_secrets import validate_forgejo_claim_inputs


def stage_qdrant_runtime_config(host: str, identity: str, environment: Path, api_key: Path) -> None:
    """Install the XeLibrary Qdrant environment and key without exposing the key.

    The remote staging directory is private to the SSH account. The key travels
    only as an SCP file payload and is installed root-owned before activation.
    """
    remote = subprocess.check_output(
        [
            "ssh",
            "-i",
            identity,
            host,
            "umask 077 && mktemp -d /var/tmp/xeonolab-qdrant-config.XXXXXXXXXX",
        ],
        text=True,
    ).strip()
    if not re.fullmatch(r"/var/tmp/xeonolab-qdrant-config\.[A-Za-z0-9]{10}", remote):
        raise RuntimeError("remote Qdrant config staging path was invalid")

    try:
        subprocess.run(
            ["scp", "-i", identity, str(environment), f"{host}:{remote}/xelibrary.env"],
            check=True,
        )
        subprocess.run(
            ["scp", "-i", identity, str(api_key), f"{host}:{remote}/qdrant-api-key"],
            check=True,
        )
        install = (
            f"sudo -n install -m 640 -o root -g xeonolab {remote}/qdrant-api-key "
            "/etc/xeonolab/qdrant-api-key && "
            f"sudo -n install -m 640 -o root -g xeonolab {remote}/xelibrary.env "
            "/etc/xeonolab/xelibrary.env"
        )
        subprocess.run(["ssh", "-i", identity, host, install], check=True)
    finally:
        subprocess.run(
            [
                "ssh",
                "-i",
                identity,
                host,
                f"rm -f {remote}/qdrant-api-key {remote}/xelibrary.env && rmdir {remote}",
            ],
            check=True,
        )


def stage_dev_agent_claim_config(
    host: str, identity: str, environment: Path, token: Path, users: Path
) -> None:
    """Install DevAgent's Forgejo claim integration as root-owned protected files."""
    validate_forgejo_claim_inputs(token, users)
    remote = subprocess.check_output(
        [
            "ssh",
            "-i",
            identity,
            host,
            "umask 077 && mktemp -d /var/tmp/xeonolab-forgejo-claim.XXXXXXXXXX",
        ],
        text=True,
    ).strip()
    if not re.fullmatch(r"/var/tmp/xeonolab-forgejo-claim\.[A-Za-z0-9]{10}", remote):
        raise RuntimeError("remote Forgejo claim staging path was invalid")

    try:
        subprocess.run(
            ["scp", "-i", identity, str(environment), f"{host}:{remote}/dev-agent.env"], check=True
        )
        subprocess.run(["scp", "-i", identity, str(token), f"{host}:{remote}/forgejo-claim-token"], check=True)
        subprocess.run(
            ["scp", "-i", identity, str(users), f"{host}:{remote}/forgejo-agent-users.json"], check=True
        )
        install = (
            "sudo -n install -d -m 750 -o root -g xeonolab /etc/xeonolab && "
            f"sudo -n install -m 640 -o root -g xeonolab {remote}/forgejo-claim-token "
            "/etc/xeonolab/forgejo-claim-token && "
            f"sudo -n install -m 640 -o root -g xeonolab {remote}/forgejo-agent-users.json "
            "/etc/xeonolab/forgejo-agent-users.json && "
            f"sudo -n install -m 640 -o root -g xeonolab {remote}/dev-agent.env "
            "/etc/xeonolab/dev-agent.env"
        )
        subprocess.run(["ssh", "-i", identity, host, install], check=True)
    finally:
        subprocess.run(
            [
                "ssh",
                "-i",
                identity,
                host,
                f"rm -f {remote}/dev-agent.env {remote}/forgejo-claim-token "
                f"{remote}/forgejo-agent-users.json && rmdir {remote}",
            ],
            check=True,
        )
