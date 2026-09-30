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
oidc_payload = Path("runtime/oidc-map-payload.json")
command(
    "uv",
    "run",
    "python",
    "infra/systemd/update_oidc_maps.py",
    "--export-env",
    "runtime/deploy-secrets/101/desktop-web.env",
    "--output",
    str(oidc_payload),
)
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
    if node == 101:
        # Update only the fixed Web 101 forced-command implementation and its
        # root-managed activation policy; activation still goes through the
        # inventory-pinned Deploy Gateway executor.
        remote_activation = "/tmp/xeonolab-activation-" + commit + ".json"
        command(
            "scp",
            "-i",
            key,
            "runtime/deploy-secrets/101/activation.json",
            host + ":" + remote_activation,
        )
        command(
            "ssh",
            "-i",
            key,
            host,
            f"sudo -n install -m 755 {release}/infra/systemd/activate.py"
            f" /usr/local/libexec/xeonolab-activate"
            f" && sudo -n install -m 644 {release}/infra/systemd/caddy_sync.py"
            f" /usr/local/libexec/caddy_sync.py"
            f" && sudo -n install -m 640 -o root -g xeonolab {remote_activation}"
            f" /etc/xeonolab/activation.json"
            f" && rm -f {remote_activation}",
        )
    remote_payload = "/tmp/xeonolab-oidc-maps-" + commit + ".json"
    command("scp", "-i", key, str(oidc_payload), host + ":" + remote_payload)
    command(
        "ssh",
        "-i",
        key,
        host,
        f"sudo -n /usr/bin/python3 {release}/infra/systemd/update_oidc_maps.py"
        f" --node {node} --payload {remote_payload} && rm -f {remote_payload}",
    )
oidc_payload.unlink(missing_ok=True)
command("scp", "-i", key, "runtime/deploy-secrets/executor.json", "xenon@192.168.0.106:/tmp/executor.json")
command(
    "ssh",
    "-i",
    key,
    "xenon@192.168.0.106",
    "sudo -n install -m 640 -o root -g xeonolab /tmp/executor.json /etc/xeonolab/executor.json",
)
print("Staged " + commit + "; activate through Deploy Gateway")
