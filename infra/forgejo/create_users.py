"""Run once with management privilege; credentials never go to command output."""

import json
import subprocess
from pathlib import Path

credentials = json.loads(Path("/tmp/forgejo-users.json").read_text())
for name, password in credentials.items():
    args = [
        "sudo",
        "-u",
        "forgejo",
        "/usr/local/bin/forgejo",
        "--config",
        "/etc/forgejo/app.ini",
        "admin",
        "user",
        "create",
        "--username",
        name,
        "--password",
        password,
        "--email",
        name + "@xenon.jyxxe.com",
        "--must-change-password=false",
    ]
    if name == "xenon-admin":
        args.append("--admin")
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode:
        raise SystemExit("User creation failed: " + name)
Path("/tmp/forgejo-users.json").unlink()
print("Independent admin, codex and opencode users created")
