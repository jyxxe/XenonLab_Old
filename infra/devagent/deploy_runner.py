#!/usr/bin/python3 -I
"""Reviewed exact-SHA deployment mechanics. Only the protected broker invokes this."""

import base64
import hashlib
import importlib.util
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

ROOT = Path("/var/lib/devagent-privileged")
REPO = "xenon-admin/XeonoLab"
TARGETS = ("101", "107", "104", "111", "106")


class Gateway:
    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=config["ca_file"])),
        )
        # Existing LAN deploy TLS name resolves to the ingress. Preserve name verification.
        original = socket.getaddrinfo

        def resolve(host, *args, **kwargs):
            name = host.decode() if isinstance(host, bytes) else host
            return original("192.168.0.101" if name == "deploy.xenon.lan" else host, *args, **kwargs)

        socket.getaddrinfo = resolve

    def raw(self, method, url, body=None, token=None, form=False):
        import urllib.parse

        data = None
        if body is not None:
            data = urllib.parse.urlencode(body).encode() if form else json.dumps(body).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded" if form else "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            with self.opener.open(
                urllib.request.Request(url, data=data, method=method, headers=headers), timeout=650
            ) as response:
                raw = response.read(8 * 1024 * 1024)
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as error:
            raise RuntimeError("Gateway HTTP " + str(error.code)) from None

    def token(self, policy=False):
        config = self.config
        cid = "devagent-deployment-policy" if policy else "devagent-maintainer-release"
        path = config["policy_secret"] if policy else config["release_secret"]
        return self.raw(
            "POST",
            config["oidc_url"],
            {
                "grant_type": "client_credentials",
                "client_id": cid,
                "client_secret": Path(path).read_text().strip(),
            },
            form=True,
        )["access_token"]

    def api(self, method, path, body=None, policy=False):
        return self.raw(method, "https://deploy.xenon.lan" + path, body, self.token(policy))["data"]


def ssh(config, node, command, bundle=None):
    with bundle.open("rb") if bundle else open(os.devnull, "rb") as source:
        result = subprocess.run(
            [
                "/usr/bin/ssh",
                "-i",
                config["stage_key"],
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "UserKnownHostsFile=" + config["known_hosts"],
                "-o",
                "ConnectTimeout=10",
                "devagent-stage@192.168.0." + node,
                command,
            ],
            stdin=source,
            capture_output=True,
            text=False,
            timeout=650,
        )
    if result.returncode:
        raise RuntimeError("target " + node + " operation failed")
    return json.loads(result.stdout)


def preflight(sha):
    spec = importlib.util.spec_from_file_location(
        "trusted_validator", "/usr/local/libexec/devagent_validate.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate(sha)


def status(config, sha, state, description):
    request = urllib.request.Request(
        config["forgejo_url"] + "/api/v1/repos/" + REPO + "/statuses/" + sha,
        data=json.dumps(
            {"state": state, "context": "devagent/preflight", "description": description[:140]}
        ).encode(),
        method="POST",
        headers={
            "Authorization": "token " + Path(config["validator_token"]).read_text().strip(),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=config["ca_file"])),
    ).open(request, timeout=30) as result:
        result.read()


def append_frontend(bundle, frontend_archive):
    # Same compiled bytes must yield the same immutable release digest across validations.
    with tarfile.open(frontend_archive) as frontend, tarfile.open(bundle, "a") as combined:
        for original in frontend.getmembers():
            if not original.name.startswith("apps/desktop-web/dist/") and original.name != "apps/desktop-web/dist":
                raise ValueError("unsafe frontend artifact")
            if not (original.isfile() or original.isdir()):
                raise ValueError("unsafe frontend artifact")
            member = original.replace(mtime=0, uid=0, gid=0, uname="", gname="")
            member.mode = 0o755 if member.isdir() else 0o644
            member.pax_headers = {}
            combined.addfile(member, frontend.extractfile(original) if original.isfile() else None)


def ensure_release_head(sha, checkout="/var/lib/devagent/checkout"):
    git = ["/usr/bin/sudo", "-n", "-H", "-u", "devagent", "/usr/bin/git",
           "--no-replace-objects", "-C", checkout]
    subprocess.run(git + ["fetch", "origin", "feature/platform-v1"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120)
    tip = subprocess.run(git + ["rev-parse", "origin/feature/platform-v1"], check=True,
                         capture_output=True, text=True, timeout=15).stdout.strip()
    if tip != sha:
        raise RuntimeError("deployment commit must equal the current production integration head")


def main():
    if os.geteuid() != 0:
        raise ValueError("root broker required")
    config = json.loads(Path("/etc/devagent/secrets/privileged.json").read_text())
    request = json.loads(sys.stdin.read(16000))
    sha = request["args"][0] if request["action"] == "validate" else request["args"][1]
    if not re.fullmatch("[a-f0-9]{40}", sha):
        raise ValueError("exact commit required")
    operation_id = os.getenv("DEVAGENT_OPERATION", "")
    if not re.fullmatch("[a-f0-9]{32}", operation_id):
        operation_id = uuid4().hex
    operation = ROOT / "deployments" / operation_id
    operation.mkdir(parents=True, mode=0o700)
    record = {
        "repository": REPO,
        "commit": sha,
        "environment": "production",
        "at": time.time(),
        "task": request.get("task"),
        "state": "preflight",
        "previous": {},
        "verification": {},
    }
    lease = None
    stop, renew_errors = threading.Event(), []
    gateway = None
    try:
        gateway = Gateway(config)
        status(config, sha, "pending", "Protected deterministic preflight is running")
        report, report_dir = preflight(sha)
        record["preflight_id"] = report["id"]
        status(
            config, sha, "success", "Protected backend, frontend and browser checks passed: " + report["id"]
        )
        if request["action"] == "validate":
            record["state"] = "validated"
            print(json.dumps({"commit": sha, "validation": report}))
            return
        checkout = "/var/lib/devagent/checkout"
        ensure_release_head(sha, checkout)
        # Validate actual PVE reachability and target versions before taking any deployment lease.
        pve_request = base64.b64encode(
            json.dumps({"action": "health", "args": [], "task": request["task"]}).encode()
        ).decode()
        checked = subprocess.run(
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
                "root@192.168.0.10",
                "devagent-pve " + pve_request,
            ],
            capture_output=True,
            timeout=30,
        )
        if checked.returncode or not json.loads(checked.stdout)["ok"]:
            raise RuntimeError("PVE preflight unavailable")
        for node in TARGETS:
            target = ssh(config, node, "devagent-stage status")
            if not target["ready"]:
                raise RuntimeError("target " + node + " is not ready before deployment")
            record["previous"][node] = target["current"]
        bundle = operation / "release.tar"
        with bundle.open("wb") as output:
            subprocess.run(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "-H",
                    "-u",
                    "devagent",
                    "/usr/bin/git",
                    "--no-replace-objects",
                    "-C",
                    checkout,
                    "archive",
                    "--format=tar",
                    sha,
                ],
                stdout=output,
                check=True,
            )
        # Root consumes a root-owned captured frontend archive, rejecting unsafe paths later on every target.
        append_frontend(bundle, report_dir / "frontend.tar")
        artifact_hash = hashlib.file_digest(bundle.open("rb"), "sha256").hexdigest()
        artifact = {"name": "platform-release", "digest": "sha256:" + artifact_hash}
        manifest = {
            "commit": sha,
            "artifacts": [artifact],
            "migration_manifest": "Automatic release only: backward-compatible catalogue, no destructive data migration; "
            "previous immutable releases are preserved. Review migrations before deployment.",
            "test_report": "Protected validation " + report["id"] + ": " + ", ".join(report["checks"]),
        }
        record["artifact"] = artifact
        rid = gateway.api("POST", "/api/v1/releases", manifest)["release_id"]
        gateway.api("POST", "/api/v1/releases/" + rid + "/approve", policy=True)
        token = gateway.token()
        claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
        lease = gateway.api(
            "POST",
            "/api/v1/deploy/lease/acquire",
            {"agent_id": claims["sub"], "release_id": rid, "ttl_seconds": 300},
        )
        record.update(release_id=rid, lease=lease, state="staging")

        def renew():
            while not stop.wait(60):
                try:
                    gateway.api(
                        "POST", "/api/v1/deploy/lease/" + lease["lease_id"] + "/renew", {"ttl_seconds": 300}
                    )
                except Exception:
                    renew_errors.append(True)
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        for node in TARGETS:
            if renew_errors:
                raise RuntimeError("deployment lease renewal failed")
            ssh(
                config,
                node,
                "devagent-stage release " + sha + " " + artifact_hash + " " + str(bundle.stat().st_size),
                bundle,
            )
        # Refuse an older merged release if another publisher advanced the branch while staging.
        ensure_release_head(sha, checkout)
        # Executor inventory update is a fixed authenticated protocol, not a free shell command.
        inventory = {"commit": sha, "artifacts": [artifact], "targets": ["192.168.0." + n for n in TARGETS]}
        inventory_file = operation / "inventory.json"
        inventory_file.write_text(json.dumps(inventory))
        ssh(config, "106", "devagent-stage inventory", inventory_file)
        record["state"] = "deploying"
        deployment = gateway.api(
            "POST",
            "/api/v1/deployments",
            {"release_id": rid, "lease_id": lease["lease_id"], "epoch": lease["epoch"]},
        )
        record["deployment"] = deployment
        if deployment["state"] not in ("completed", "rolled_back"):
            record["recovery_required"] = True
            raise RuntimeError("executor ended unresolved; preserve lease and require recovery")
        for node in TARGETS:
            target = ssh(config, node, "devagent-stage status")
            actual = target["current"]
            expected = sha if deployment["state"] == "completed" else record["previous"][node]
            record["verification"][node] = {
                "expected": expected,
                "actual": actual,
                "ok": actual == expected and target["ready"],
            }
        if deployment["state"] == "completed" and all(v["ok"] for v in record["verification"].values()):
            try:
                result = ssh(config, "106", "devagent-stage gateway " + sha + " " + deployment["deployment_id"])
                record["verification"]["gateway"] = {"ok": result["ready"] and result["gateway"] == sha}
            except Exception:
                record["verification"]["gateway"] = {"ok": False}
        if not all(v["ok"] for v in record["verification"].values()):
            rollback_ok = True
            for node in reversed(TARGETS):
                try:
                    ssh(config, node, "devagent-stage rollback " + deployment["deployment_id"])
                    target = ssh(config, node, "devagent-stage status")
                    rollback_ok = (
                        rollback_ok and target["current"] == record["previous"][node] and target["ready"]
                    )
                except Exception:
                    rollback_ok = False
            if "gateway" in record["verification"]:
                try:
                    result = ssh(config, "106", "devagent-stage gateway " + record["previous"]["106"]
                                 + " " + deployment["deployment_id"])
                    rollback_ok = rollback_ok and result["ready"] and result["gateway"] == record["previous"]["106"]
                except Exception:
                    rollback_ok = False
            record["rollback_verified"] = rollback_ok
            record["recovery_required"] = not rollback_ok
            raise RuntimeError("postcheck failed; rollback attempted; operator audit required")
        record["state"] = deployment["state"]
        gateway.api("POST", "/api/v1/deploy/lease/" + lease["lease_id"] + "/release")
        lease = None
        print(json.dumps(record))
    except Exception:
        if record["state"] == "preflight":
            status(config, sha, "failure", "Protected preflight failed; deployment was not activated")
        record["state_before_failure"] = record["state"]
        record["state"] = "failed"
        raise
    finally:
        stop.set()
        if lease and gateway:
            # A staging failure has not invoked activation; a verified rollback is also safe.
            safe_release = not record.get("deployment") and record.get("state_before_failure") == "staging"
            safe_release = safe_release or record.get("rollback_verified", False)
            if safe_release:
                try:
                    gateway.api("POST", "/api/v1/deploy/lease/" + lease["lease_id"] + "/release")
                    record["lease_released"] = True
                except Exception:
                    record["recovery_required"] = True
            else:
                record["recovery_required"] = True
        record["finished_at"] = time.time()
        (operation / "audit.json").write_text(json.dumps(record, indent=2))


if __name__ == "__main__":
    os.umask(0o077)
    try:
        main()
    except Exception:
        print("Protected preflight/deployment failed; inspect root audit", file=sys.stderr)
        sys.exit(2)
