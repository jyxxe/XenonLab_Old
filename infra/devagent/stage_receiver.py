#!/usr/bin/python3 -I
"""Project target forced-command staging receiver. Application build runs without root."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path("/opt/xeonolab/releases")


def safe_members(archive):
    members = archive.getmembers()
    for member in members:
        path = Path(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise ValueError("unsafe archive path")
        if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
            raise ValueError("archive links/devices are forbidden")
        if path.parts[0] in (".venv", ".git", "runtime"):
            raise ValueError("runtime or credentials must not enter release archives")
    return members


def main():
    parts = os.getenv("SSH_ORIGINAL_COMMAND", "").split()
    if parts == ["devagent-stage", "status"]:
        current = Path("/opt/xeonolab/current").resolve()
        config = json.loads(Path("/etc/xeonolab/activation.json").read_text())
        ready = True
        for port in config["ports"]:
            try:
                with urllib.request.urlopen(
                    f"http://{config['address']}:{port}/health/ready", timeout=5
                ) as response:
                    ready = ready and response.status == 200
            except Exception:
                ready = False
        print(
            json.dumps(
                {
                    "current": current.name,
                    "services": config,
                    "ready": ready,
                }
            )
        )
        return
    if parts == ["devagent-stage", "inventory"]:
        inventory = json.loads(sys.stdin.read(65536))
        if set(inventory) != {"commit", "artifacts", "targets"}:
            raise ValueError("invalid executor inventory")
        sha = inventory["commit"]
        if not re.fullmatch("[a-f0-9]{40}", sha) or inventory["targets"] != [
            "192.168.0." + n for n in ("101", "107", "104", "111", "106")
        ]:
            raise ValueError("fixed deployment inventory required")
        stamp = json.loads((ROOT / sha / ".devagent-artifact.json").read_text())
        if inventory["artifacts"] != [{"name": "platform-release", "digest": "sha256:" + stamp["digest"]}]:
            raise ValueError("staged artifact mismatch")
        temporary = Path("/etc/xeonolab/executor.devagent-next")
        temporary.write_text(json.dumps(inventory))
        subprocess.run(["/usr/bin/chown", "root:xeonolab", str(temporary)], check=True)
        temporary.chmod(0o640)
        os.replace(temporary, "/etc/xeonolab/executor.json")
        print(json.dumps({"inventory": sha}))
        return
    if len(parts) == 3 and parts[:2] == ["devagent-stage", "rollback"]:
        if not re.fullmatch("[a-f0-9-]{36}", parts[2]):
            raise ValueError("deployment UUID required")
        result = subprocess.run(
            ["/usr/local/libexec/xeonolab-activate"],
            input="rollback " + parts[2],
            text=True,
            capture_output=True,
            timeout=180,
        )
        if result.returncode:
            raise RuntimeError("rollback receiver failed")
        print(json.dumps({"rolled_back": parts[2]}))
        return
    if len(parts) != 5 or parts[:2] != ["devagent-stage", "release"]:
        raise ValueError("fixed staging protocol required")
    sha, digest, length = parts[2], parts[3], int(parts[4])
    if not re.fullmatch("[a-f0-9]{40}", sha) or not re.fullmatch("[a-f0-9]{64}", digest):
        raise ValueError("exact commit/digest required")
    if not 0 < length <= 512 * 1024 * 1024:
        raise ValueError("release size out of bounds")
    ROOT.mkdir(parents=True, exist_ok=True)
    target = ROOT / sha
    if target.exists():
        stamp = target / ".devagent-artifact.json"
        if stamp.exists() and json.loads(stamp.read_text())["digest"] == digest:
            print(json.dumps({"staged": sha, "already_present": True}))
            return
        raise ValueError("existing release cannot be overwritten")
    staging = Path(tempfile.mkdtemp(prefix="devagent-stage-", dir="/var/tmp"))
    created_target = False
    try:
        bundle = staging / "release.tar"
        remaining = length
        with bundle.open("wb") as output:
            while remaining:
                chunk = sys.stdin.buffer.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("truncated release")
                output.write(chunk)
                remaining -= len(chunk)
        if hashlib.file_digest(bundle.open("rb"), "sha256").hexdigest() != digest:
            raise ValueError("release digest mismatch")
        extracted = staging / "source"
        extracted.mkdir()
        with tarfile.open(bundle) as archive:
            archive.extractall(extracted, members=safe_members(archive), filter="data")
        # Rename before uv sync: editable install paths must refer to the final immutable path.
        os.rename(extracted, target)
        created_target = True
        subprocess.run(["/usr/bin/chown", "-R", "xeonolab:xeonolab", str(target)], check=True)
        argv = [
            "/usr/bin/sudo",
            "-n",
            "-H",
            "-u",
            "xeonolab",
            "/usr/bin/env",
            "UV_PYTHON_INSTALL_DIR=/opt/xeonolab/python",
            "/usr/local/bin/uv",
            "sync",
            "--locked",
            "--no-dev",
            "--python",
            "3.12",
        ]
        if json.loads(Path("/etc/xeonolab/activation.json").read_text())["address"] == "192.168.0.107":
            argv += ["--extra", "inference"]
        result = subprocess.run(argv, cwd=target, capture_output=True, timeout=600)
        if result.returncode:
            raise RuntimeError("unprivileged application dependency preparation failed")
        stamp = target / ".devagent-artifact.json"
        stamp.write_text(json.dumps({"commit": sha, "digest": digest}))
        subprocess.run(["/usr/bin/chown", "-R", "root:root", str(target)], check=True)
        print(json.dumps({"staged": sha, "digest": digest}))
    except Exception:
        # No activation occurred. Remove only the new staging target created by this receiver.
        if created_target and target.exists():
            shutil.rmtree(target)
        raise
    finally:
        shutil.rmtree(staging)


if __name__ == "__main__":
    os.umask(0o022)
    try:
        main()
    except (ValueError, KeyError, RuntimeError, OSError, subprocess.TimeoutExpired):
        print("Release staging rejected or failed", file=sys.stderr)
        sys.exit(2)
