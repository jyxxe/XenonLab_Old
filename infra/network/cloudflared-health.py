#!/usr/bin/python3
"""Recover a disconnected connector; partial degradation does not trigger restart."""
import json
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen


def next_state(previous, healthy, now):
    if healthy:
        return {**previous, "failed_since": None}, False
    since = previous.get("failed_since")
    since = now if since is None else since
    restart = now - since >= 90 and now - previous.get("last_restart", 0) >= 300
    return {
        "failed_since": now if restart else since,
        "last_restart": now if restart else previous.get("last_restart", 0),
    }, restart


def main():
    state_file = Path("/run/cloudflared-health/state.json")
    state_file.parent.mkdir(mode=0o700, exist_ok=True)
    previous = json.loads(state_file.read_text()) if state_file.exists() else {}
    try:
        with urlopen("http://127.0.0.1:20241/ready", timeout=5) as result:
            healthy = result.status == 200 and json.load(result).get("readyConnections", 0) > 0
    except (OSError, ValueError):
        healthy = False
    state, restart = next_state(previous, healthy, time.monotonic())
    state_file.write_text(json.dumps(state))
    if restart:
        print("No ready tunnel connections for 90 seconds; restarting connector", flush=True)
        subprocess.run(["systemctl", "restart", "cloudflared.service"], check=True, timeout=45)


if __name__ == "__main__":
    main()
