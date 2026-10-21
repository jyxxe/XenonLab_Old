#!/usr/bin/python3 -I
"""Project target forced-command staging receiver. Application build runs without root."""

import fcntl
import hashlib
import json
import math
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


def _gateway_main_pid():
    result = subprocess.run(
        ["/usr/bin/systemctl", "show", "xeonolab@deploy-gateway.service", "--property=MainPID", "--value", "--no-pager"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    value = result.stdout.strip()
    if not re.fullmatch(r"[1-9][0-9]{0,8}", value):
        raise ValueError("gateway main PID unavailable")
    return int(value)


def _gateway_runtime_cwd(pid):
    if type(pid) is not int or pid < 1:
        raise ValueError("invalid gateway PID")
    return Path(f"/proc/{pid}/cwd").resolve(strict=True)


def _gateway_ready_probe():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://192.168.0.106:8103/health/ready", timeout=5) as response:
        if type(response.status) is not int or response.status != 200:
            raise RuntimeError("gateway readiness failed")
        body = response.read(4097)
        if len(body) > 4096:
            raise ValueError("gateway readiness response is oversized")
        payload = json.loads(body)
        if payload != {"status": "ready", "service": "deploy-gateway"}:
            raise ValueError("gateway readiness response is malformed")
        return True


def publication_recovery():
    """Read-only proof for reconciling a merged publication with VM106 runtime state."""
    activation = json.loads(ACTIVATION.read_text())
    if not isinstance(activation, dict) or activation.get("address") != "192.168.0.106":
        raise ValueError("gateway target required")

    releases_root = ROOT.resolve(strict=True)
    current = PLATFORM / "current"
    current_path = current.resolve(strict=True)
    gateway_commit = current_path.name
    if (
        not re.fullmatch(r"[a-f0-9]{40}", gateway_commit)
        or current_path.parent != releases_root
        or not current_path.is_dir()
    ):
        raise ValueError("current immutable gateway release is invalid")

    with sqlite3.connect("file:" + str(DEPLOY_DATABASE) + "?mode=ro", uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        query_only = db.execute("PRAGMA query_only").fetchone()
        if not query_only or query_only[0] != 1:
            raise ValueError("deployment database is not query-only")
        db.execute("BEGIN")
        lease_columns = {row[1] for row in db.execute("PRAGMA table_info(lease)")}
        if not {"singleton", "id", "holder", "release_id", "epoch", "expires"}.issubset(lease_columns):
            raise ValueError("deployment lease schema is incomplete")
        deployment_columns = {row[1] for row in db.execute("PRAGMA table_info(deployments)")}
        if "state" not in deployment_columns:
            raise ValueError("deployment state schema is incomplete")
        lease = db.execute(
            "SELECT id,holder,release_id,epoch,expires FROM lease WHERE singleton=1"
        ).fetchall()
        if len(lease) != 1:
            raise ValueError("deployment lease row is missing or ambiguous")
        lease_id, holder, release_id, epoch, expires = lease[0]
        if type(epoch) is not int or epoch < 0:
            raise ValueError("deployment lease epoch is invalid")
        lease_fields = (lease_id, holder, release_id, expires)
        if all(value is None for value in lease_fields):
            lease_proof = {"id": None, "holder": None, "release_id": None, "epoch": epoch, "expires": None}
        else:
            if (
                not isinstance(lease_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", lease_id)
                or not isinstance(holder, str) or not re.fullmatch(r"[A-Za-z0-9_.:@/-]{1,256}", holder)
                or not isinstance(release_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", release_id)
                or isinstance(expires, bool) or not isinstance(expires, (int, float))
                or not math.isfinite(expires) or expires <= 0
            ):
                raise ValueError("deployment lease fields are malformed")
            lease_proof = {
                "id": lease_id,
                "holder": holder,
                "release_id": release_id,
                "epoch": epoch,
                "expires": expires,
            }
        unresolved = db.execute(
            "SELECT COUNT(*) FROM deployments WHERE state IS NULL OR state NOT IN ('completed','rolled_back','cancelled')"
        ).fetchone()
        if not unresolved or type(unresolved[0]) is not int or unresolved[0] < 0:
            raise ValueError("unresolved deployment count is invalid")
        unresolved_count = unresolved[0]

    pid = _gateway_main_pid()
    runtime_path = _gateway_runtime_cwd(pid)
    if runtime_path != current_path or runtime_path.parent != releases_root:
        raise ValueError("gateway process is not running the current immutable release")
    runtime_commit = runtime_path.name
    if not re.fullmatch(r"[a-f0-9]{40}", runtime_commit):
        raise ValueError("gateway runtime commit is invalid")
    _gateway_ready_probe()
    return {
        "gateway_commit": gateway_commit,
        "runtime_commit": runtime_commit,
        "ready": True,
        "lease": lease_proof,
        "unresolved_deployments": unresolved_count,
    }


def main():
    parts = os.getenv("SSH_ORIGINAL_COMMAND", "").split()
    if parts == ["devagent-stage", "publication-recovery"]:
        print(json.dumps(publication_recovery(), sort_keys=True))
        return
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
