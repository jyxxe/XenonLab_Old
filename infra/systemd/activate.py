#!/usr/bin/python3
"""Root-owned forced-command activation receiver. No arbitrary command input."""

import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import caddy_sync

parts = sys.stdin.read(256).strip().split()
if not parts or parts[0] not in ("activate", "rollback"):
    raise SystemExit(2)

config = json.loads(Path("/etc/xeonolab/activation.json").read_text())
root = Path("/opt/xeonolab")
current = root / "current"
state_dir = root / "activations"
state_dir.mkdir(exist_ok=True)
sync_cloudflare_caddy = config.get("reload_caddy") is True
cloudflare_changed = False

if parts[0] == "activate":
    if (
        len(parts) != 3
        or not re.fullmatch("[a-f0-9]{40}", parts[1])
        or not re.fullmatch("[a-f0-9-]{36}", parts[2])
    ):
        raise SystemExit(2)
    commit, deployment_id = parts[1:]
    target = root / "releases" / commit
    if not (target / ".venv/bin/uvicorn").exists():
        raise SystemExit(3)
    old = str(current.resolve()) if current.is_symlink() else None
    include_snapshot = caddy_sync.capture().as_json() if sync_cloudflare_caddy else None
    state = {"old": old, "new": str(target), "old_cloudflare_include": include_snapshot}
    state_path = state_dir / f"{deployment_id}.json"
    caddy_sync.atomic_write_json(state_path, state)
else:
    if len(parts) != 2 or not re.fullmatch("[a-f0-9-]{36}", parts[1]):
        raise SystemExit(2)
    saved = json.loads((state_dir / f"{parts[1]}.json").read_text())
    target = Path(saved["old"]) if saved["old"] else None
    include_snapshot = saved.get("old_cloudflare_include")
    rollback_current_include = caddy_sync.capture().as_json() if sync_cloudflare_caddy else None


def restart(destination, cloudflare_release=None, cloudflare_snapshot=None):
    global cloudflare_changed

    def update_cloudflare():
        global cloudflare_changed

        def note_cloudflare_change():
            global cloudflare_changed
            cloudflare_changed = True

        if not sync_cloudflare_caddy:
            return
        if cloudflare_release is not None:
            candidate = cloudflare_release / "infra/network/cloudflare-origin.caddy"
            changed = caddy_sync.install(candidate, on_replace=note_cloudflare_change)
        elif cloudflare_snapshot is not None:
            changed = caddy_sync.restore(cloudflare_snapshot, on_replace=note_cloudflare_change)
        else:
            return
        if changed:
            note_cloudflare_change()
            subprocess.run(["systemctl", "reload", "caddy"], check=True)

    if destination is None:
        for service in config["services"]:
            subprocess.run(["systemctl", "stop", "xeonolab@" + service], check=True)
        current.unlink(missing_ok=True)
        update_cloudflare()
        return
    if destination.parent != root / "releases":
        raise SystemExit(3)
    temporary = root / "next"
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(destination)
    os.replace(temporary, current)
    for service in config["services"]:
        subprocess.run(
            ["systemctl", "enable", "xeonolab@" + service],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(["systemctl", "restart", "xeonolab@" + service], check=True)
    for port in config["ports"]:
        for attempt in range(120):
            try:
                with urllib.request.urlopen(
                    f"http://{config['address']}:{port}/health/ready", timeout=3
                ) as result:
                    if result.status == 200:
                        break
            except Exception:
                pass
            time.sleep(0.5)
        else:
            raise RuntimeError("health check failed")
    update_cloudflare()


try:
    restart(
        target,
        cloudflare_release=target if parts[0] == "activate" and sync_cloudflare_caddy else None,
        cloudflare_snapshot=include_snapshot if parts[0] == "rollback" else None,
    )
except Exception:
    if parts[0] == "activate":
        restart(
            Path(old) if old else None,
            cloudflare_snapshot=include_snapshot if sync_cloudflare_caddy and cloudflare_changed else None,
        )
    else:
        restart(
            Path(saved["new"]),
            cloudflare_snapshot=(
                rollback_current_include if sync_cloudflare_caddy and cloudflare_changed else None
            ),
        )
    raise SystemExit(1)
print("activation confirmed")
