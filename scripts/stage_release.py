"""Stage an immutable release using existing management access, without activation."""

import subprocess
from pathlib import Path


def command(*args):
    return subprocess.run(args, check=True)


commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
    raise SystemExit("Commit tracked changes before staging")
key = str(Path.home() / ".ssh/xenon_v2_ed25519")
command("git", "archive", "--format=tar", "HEAD", "-o", "runtime/xeonolab-release.tar")
command("tar", "-rf", "runtime/xeonolab-release.tar", "apps/desktop-web/dist")
command("uv", "run", "python", "scripts/prepare_deployment.py")
for node in (107, 104, 101, 111, 106):
    host = f"xenon@192.168.0.{node}"
    command("scp", "-i", key, "runtime/xeonolab-release.tar", host + ":/tmp/xeonolab-release.tar")
    release = "/opt/xeonolab/releases/" + commit
    extra = " --extra inference" if node == 107 else ""
    # commit comes from git, fixed node inventory; no user payload is shell input.
    script = (
        f"sudo -n mkdir -p {release} && sudo -n tar -xf /tmp/xeonolab-release.tar -C {release}"
        f" && cd {release} && sudo -n env UV_PYTHON_INSTALL_DIR=/opt/xeonolab/python"
        f" /usr/local/bin/uv sync --locked --no-dev --python 3.12{extra}"
    )
    command("ssh", "-i", key, host, script)
command("scp", "-i", key, "runtime/deploy-secrets/executor.json", "xenon@192.168.0.106:/tmp/executor.json")
command(
    "ssh",
    "-i",
    key,
    "xenon@192.168.0.106",
    "sudo -n install -m 640 -o root -g xeonolab /tmp/executor.json /etc/xeonolab/executor.json",
)
print("Staged " + commit + "; activate through Deploy Gateway")
