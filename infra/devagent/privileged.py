#!/usr/bin/python3 -I
"""Root-owned capability boundary. No project code is imported or executed as root."""

import base64
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path("/var/lib/devagent-privileged")
CONFIG = Path("/etc/devagent/secrets/privileged.json")


def request_valid(request):
    if set(request) - {"action", "args", "task"}:
        raise ValueError("unexpected request fields")
    if not re.fullmatch("[A-Za-z0-9-]{1,64}", request.get("task", "manual")):
        raise ValueError("invalid task identifier")
    args = request.get("args", [])
    if (
        not isinstance(args, list)
        or len(args) > 8
        or any(not isinstance(a, str) or len(a) > 1024 for a in args)
    ):
        raise ValueError("bounded string arguments required")
    if request["action"] == "validate":
        if len(args) != 1 or not re.fullmatch("[a-f0-9]{40}", args[0]):
            raise ValueError("exact validation commit required")
    elif request["action"] == "deploy":
        if len(args) != 3 or args[0] != "xenon-admin/XeonoLab" or args[2] != "production":
            raise ValueError("fixed production repository required")
        if not re.fullmatch("[a-f0-9]{40}", args[1]):
            raise ValueError("exact commit required")
    elif request["action"] != "infrastructure":
        raise ValueError("unsupported privileged operation")


def pve(config, request):
    args = request["args"]
    if not args:
        raise ValueError("infrastructure action required")
    reason = ""
    action, values = args[0], args[1:]
    if action in ("start", "snapshot", "resources", "grow-disk"):
        if not values:
            raise ValueError("change requires arguments and reason")
        reason, values = values[-1], values[:-1]
    payload = {"action": action, "args": values, "reason": reason, "task": request.get("task", "manual")}
    encoded = base64.b64encode(json.dumps(payload).encode()).decode()
    result = subprocess.run(
        [
            "/usr/bin/ssh",
            "-i",
            config["pve_key"],
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=" + config["known_hosts"],
            "-o",
            "ConnectTimeout=10",
            "root@192.168.0.10",
            "devagent-pve " + encoded,
        ],
        capture_output=True,
        text=True,
        timeout=650,
    )
    if result.returncode:
        raise RuntimeError("PVE operation rejected or failed")
    return json.loads(result.stdout)


def main():
    if os.geteuid() != 0 or os.getenv("SUDO_USER") != "devagent":
        raise ValueError("dedicated DevAgent sudo caller required")
    request = json.loads(sys.stdin.read(16000))
    request_valid(request)
    config = json.loads(CONFIG.read_text())
    os.environ.clear()
    os.environ.update(PATH="/usr/local/bin:/usr/bin:/bin", HOME="/root", LANG="C.UTF-8")
    ROOT.mkdir(mode=0o700, exist_ok=True)
    record = {
        "id": str(uuid4()),
        "at": time.time(),
        "task": request.get("task"),
        "action": request["action"],
        "repository": "xenon-admin/XeonoLab",
    }
    if request["action"] == "deploy":
        record.update(commit=request["args"][1], environment=request["args"][2])
    with (ROOT / "operation.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            if request["action"] == "infrastructure":
                result = pve(config, request)
            else:
                # Execute installed trusted mechanics; project code runs in unprivileged validation units.
                runner = "/usr/local/libexec/devagent-deploy"
                completed = subprocess.run(
                    [runner], input=json.dumps(request), text=True, capture_output=True, timeout=3600
                )
                if completed.returncode:
                    raise RuntimeError("production deployment failed; inspect protected audit")
                result = json.loads(completed.stdout)
            record["result"] = "completed"
            print(json.dumps(result))
        except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
            record["result"] = "failed"
            raise
        finally:
            with (ROOT / "operations.jsonl").open("a") as file:
                file.write(json.dumps(record) + "\n")


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except (ValueError, KeyError, RuntimeError, OSError, subprocess.TimeoutExpired):
        print("Privileged operation rejected or failed", file=sys.stderr)
        sys.exit(2)
