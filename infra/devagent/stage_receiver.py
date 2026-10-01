#!/usr/bin/python3 -I
"""Project target forced-command staging receiver. Application build runs without root."""

import fcntl
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path("/opt/xeonolab/releases")
PLATFORM = Path("/opt/xeonolab")
ACTIVATION = Path("/etc/xeonolab/activation.json")
DEPLOY_DATABASE = Path("/var/lib/xeonolab/deploy-gateway/deploy.sqlite")


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


def restart_gateway(sha, deployment_id):
    # Restart only after the in-flight executor returned, while its lease still excludes others.
    if not re.fullmatch("[a-f0-9]{40}", sha) or not re.fullmatch("[a-f0-9-]{36}", deployment_id):
        raise ValueError("exact gateway deployment required")
    if json.loads(ACTIVATION.read_text())["address"] != "192.168.0.106":
        raise ValueError("gateway target required")
    current = PLATFORM / "current"
    expected = ROOT / sha
    saved = json.loads((PLATFORM / "activations" / (deployment_id + ".json")).read_text())
    if current.resolve() != expected or str(expected) not in (saved["new"], saved["old"]):
        raise ValueError("gateway activation mismatch")
    with sqlite3.connect("file:" + str(DEPLOY_DATABASE) + "?mode=ro", uri=True) as db:
        deployment = db.execute(
            "SELECT holder,epoch,state,release_id FROM deployments WHERE id=?", (deployment_id,)
        ).fetchone()
        release = db.execute("SELECT manifest,approved FROM releases WHERE id=?", (deployment[3],)).fetchone() if deployment else None
        lease = db.execute("SELECT holder,epoch,expires,release_id FROM lease WHERE singleton=1").fetchone()
        unresolved = db.execute(
            "SELECT 1 FROM deployments WHERE state NOT IN ('completed','rolled_back','cancelled')"
        ).fetchone()
        if (
            not deployment or deployment[2] not in ("completed", "rolled_back") or unresolved
            or not release or not release[1]
            or not lease or lease[:2] != deployment[:2] or lease[3] != deployment[3]
            or (lease[2] or 0) <= time.time()
        ):
            raise ValueError("terminal deployment with its active lease required")
        manifest = json.loads(release[0])
        activation_targets = {Path(saved[key]).name for key in ("new", "old") if saved.get(key)}
        if expected.name not in activation_targets or manifest.get("commit") != Path(saved["new"]).name:
            raise ValueError("gateway SHA differs from the approved release")
    subprocess.run(
        ["/usr/bin/systemctl", "restart", "xeonolab@deploy-gateway.service"], check=True, timeout=60
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for _ in range(60):
        try:
            with opener.open("http://192.168.0.106:8103/health/ready", timeout=1) as response:
                if response.status == 200:
                    return {"gateway": sha, "ready": True}
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.5)
    raise RuntimeError("gateway readiness failed")


def main():
    parts = os.getenv("SSH_ORIGINAL_COMMAND", "").split()
    if len(parts) == 4 and parts[:2] == ["devagent-stage", "gateway"]:
        print(json.dumps(restart_gateway(parts[2], parts[3])))
        return
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
        if os.geteuid() != 0:
            raise ValueError("root receiver required")
        with Path("/var/lock/devagent-stage.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except (ValueError, KeyError, RuntimeError, OSError, sqlite3.Error, subprocess.TimeoutExpired):
        print("Release staging rejected or failed", file=sys.stderr)
        sys.exit(2)
