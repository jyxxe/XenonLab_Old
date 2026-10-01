#!/usr/bin/python3 -I
"""PVE root forced-command receiver, restricted to the XeonoLab VM inventory."""

import base64
import fcntl
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

VMS = frozenset(range(100, 112))


def command_for(request):
    action = request["action"]
    args = request.get("args", [])
    if action == "health" and not args:
        return ["/usr/bin/pvesh", "get", "/cluster/resources", "--type", "vm", "--output-format", "json"]
    if action == "pool" and not args:
        return ["/usr/sbin/zpool", "list", "-Hp", "-o", "name,size,alloc,free,health"]
    vm = int(args[0])
    if vm not in VMS:
        raise ValueError("VM outside XeonoLab inventory")
    if action in ("status", "config", "snapshots") and len(args) == 1:
        return [
            "/usr/sbin/qm",
            {"status": "status", "config": "config", "snapshots": "listsnapshot"}[action],
            str(vm),
        ]
    # Changes require a concrete reason and use fixed commands, never an arbitrary root shell.
    if len(request.get("reason", "")) < 16:
        raise ValueError("infrastructure change requires a project reason")
    if action == "start" and len(args) == 1:
        return ["/usr/sbin/qm", "start", str(vm)]
    if action == "snapshot" and len(args) == 2 and re.fullmatch("devagent-[a-z0-9-]{1,40}", args[1]):
        return [
            "/usr/sbin/qm",
            "snapshot",
            str(vm),
            args[1],
            "--vmstate",
            "0",
            "--description",
            request["reason"][:512],
        ]
    if action == "resources" and len(args) == 3:
        cpus, memory = int(args[1]), int(args[2])
        if 1 <= cpus <= 32 and 1024 <= memory <= 98304 and memory % 1024 == 0:
            return ["/usr/sbin/qm", "set", str(vm), "--cores", str(cpus), "--memory", str(memory)]
    if action == "grow-disk" and len(args) == 3:
        if re.fullmatch("(?:scsi|virtio)[0-9]", args[1]) and re.fullmatch(r"\+[1-9][0-9]{0,3}G", args[2]):
            return ["/usr/sbin/qm", "resize", str(vm), args[1], args[2]]
    raise ValueError("unsupported infrastructure action or arguments")


def main():
    original = os.getenv("SSH_ORIGINAL_COMMAND", "")
    if not original.startswith("devagent-pve ") or len(original) > 12000:
        raise ValueError("forced-command request required")
    request = json.loads(base64.b64decode(original.split(" ", 1)[1], validate=True))
    command = command_for(request)
    root = Path("/var/log/xeonolab-devagent")
    root.mkdir(mode=0o700, exist_ok=True)
    with (root / "pve.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = subprocess.run(command, capture_output=True, text=True, timeout=600)
        record = {
            "at": time.time(),
            "action": request["action"],
            "args": request.get("args"),
            "task": request.get("task"),
            "result": result.returncode,
            "reason_digest": hashlib.sha256(request.get("reason", "").encode()).hexdigest(),
        }
        with (root / "pve.jsonl").open("a") as audit:
            audit.write(json.dumps(record) + "\n")
        if result.returncode:
            # The privileged stderr can contain configuration data. Keep the public error minimal.
            print(json.dumps({"ok": False, "exit_code": result.returncode}))
        else:
            print(json.dumps({"ok": True, "output": result.stdout}))
        return result.returncode


if __name__ == "__main__":
    os.umask(0o077)
    try:
        sys.exit(main())
    except (ValueError, IndexError, KeyError, OSError, subprocess.TimeoutExpired):
        print("PVE request rejected or unavailable", file=sys.stderr)
        sys.exit(2)
