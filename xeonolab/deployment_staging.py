"""Coordinator-only transfer of protected runtime configuration."""

import re
import subprocess
from pathlib import Path


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
