#!/usr/bin/python3 -I
"""Root-owned capability boundary. No project code is imported or executed as root."""

import base64
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path("/var/lib/devagent-privileged")
CONFIG = Path("/etc/devagent/secrets/privileged.json")
RUNNER = "/usr/local/libexec/devagent-deploy"
BROKER = "/usr/local/libexec/devagent-privileged"


def job_path(identifier):
    if not isinstance(identifier, str) or not re.fullmatch("[a-f0-9]{32}", identifier):
        raise ValueError("exact operation identifier required")
    return ROOT / "jobs" / identifier


def publication_recovery(identifier):
    """Read a root-issued abort receipt without changing the failed operation."""
    job_path(identifier)
    directory = ROOT / "publication-recoveries"
    try:
        directory_info = directory.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISDIR(directory_info.st_mode) or directory_info.st_uid != 0
            or stat.S_IMODE(directory_info.st_mode) != 0o700):
        raise ValueError("unsafe publication recovery directory")
    path = directory / (identifier + ".json")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_size > 16384):
        raise ValueError("unsafe publication recovery receipt")
    receipt = json.loads(path.read_text())
    required = {
        "receipt_id", "state", "operation_id", "number", "head", "commit",
        "baseline_commit", "evidence",
    }
    if (not isinstance(receipt, dict) or set(receipt) != required
            or receipt.get("state") != "operator_aborted" or receipt.get("operation_id") != identifier
            or not isinstance(receipt.get("receipt_id"), str)
            or not re.fullmatch("[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}", receipt["receipt_id"])
            or not isinstance(receipt.get("number"), int) or isinstance(receipt["number"], bool)
            or receipt["number"] <= 0
            or any(not isinstance(receipt.get(key), str)
                   or not re.fullmatch("[a-f0-9]{40}", receipt[key])
                   for key in ("head", "commit", "baseline_commit"))):
        raise ValueError("invalid publication recovery receipt")
    evidence = receipt["evidence"]
    if (not isinstance(evidence, dict)
            or set(evidence) != {"native_job_sha256", "native_audit_sha256", "targets_verified",
                                 "gateway_unresolved", "lease_active"}
            or any(not isinstance(evidence.get(key), str)
                   or not re.fullmatch("[a-f0-9]{64}", evidence[key])
                   for key in ("native_job_sha256", "native_audit_sha256"))
            or evidence.get("targets_verified") is not True
            or evidence.get("gateway_unresolved") is not False
            or evidence.get("lease_active") is not False):
        raise ValueError("invalid publication recovery evidence")

    # Bind the receipt to the immutable failed DevAgent job and its runner audit.
    directory = job_path(identifier)
    status_path, request_path = directory / "status.json", directory / "request.json"
    audit_directory = ROOT / "deployments" / identifier
    audit_path = audit_directory / "audit.json"
    for evidence_directory in (ROOT, ROOT / "jobs", directory, ROOT / "deployments", audit_directory):
        evidence_dir_info = evidence_directory.lstat()
        if (not stat.S_ISDIR(evidence_dir_info.st_mode) or evidence_dir_info.st_uid != 0
                or evidence_dir_info.st_mode & 0o077):
            raise ValueError("unsafe protected recovery evidence directory")
    for evidence_path in (status_path, request_path, audit_path):
        evidence_info = evidence_path.lstat()
        if (not stat.S_ISREG(evidence_info.st_mode) or evidence_info.st_uid != 0
                or evidence_info.st_mode & 0o022):
            raise ValueError("unsafe protected recovery evidence")
    status_bytes, audit_bytes = status_path.read_bytes(), audit_path.read_bytes()
    if (evidence["native_job_sha256"] != hashlib.sha256(status_bytes).hexdigest()
            or evidence["native_audit_sha256"] != hashlib.sha256(audit_bytes).hexdigest()):
        raise ValueError("publication recovery evidence changed")
    status = json.loads(status_bytes)
    request = json.loads(request_path.read_text())
    audit = json.loads(audit_bytes)
    if (status.get("id") != identifier or status.get("state") != "failed"
            or status.get("action") != "deploy" or status.get("recovery_required") is not False
            or status.get("commit") != receipt["commit"]
            or request.get("action") != "deploy"
            or request.get("args") != ["xenon-admin/XeonoLab", receipt["commit"], "production"]
            or audit.get("repository") != "xenon-admin/XeonoLab"
            or audit.get("commit") != receipt["commit"]
            or audit.get("environment") != "production"
            or audit.get("state_before_failure") != "preflight"
            or audit.get("state") != "failed" or audit.get("previous") != {}
            or audit.get("verification") != {} or audit.get("recovery_required") is True
            or any(key in audit for key in ("release_id", "lease", "artifact", "deployment"))):
        raise ValueError("receipt does not match a failed pre-activation publication")
    return receipt


def save(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value))
    temporary.replace(path)


def service_active(identifier):
    return (
        subprocess.run(
            ["/usr/bin/systemctl", "is-active", "--quiet", "devagent-operation-" + identifier],
            capture_output=True,
            timeout=10,
        ).returncode
        == 0
    )


def job_status(identifier):
    directory = job_path(identifier)
    record = json.loads((directory / "status.json").read_text())
    if record["state"] in ("queued", "running") and not service_active(identifier):
        # Never replay a deployment after a crash or reboot: activation may have happened.
        record.update(state="interrupted", finished_at=time.time(), recovery_required=True)
        save(directory / "status.json", record)
    return record


def submit(request):
    jobs = ROOT / "jobs"
    jobs.mkdir(mode=0o700, exist_ok=True)
    for directory in jobs.iterdir():
        if not re.fullmatch("[a-f0-9]{32}", directory.name):
            continue
        previous = job_status(directory.name)
        if previous["state"] in ("queued", "running"):
            raise RuntimeError("another protected operation is active")
        if previous.get("recovery_required") and previous["action"] == "deploy":
            raise RuntimeError("interrupted deployment requires operator recovery")
    identifier = uuid4().hex
    directory = job_path(identifier)
    directory.mkdir(mode=0o700)
    save(directory / "request.json", request)
    record = {
        "id": identifier,
        "state": "queued",
        "action": request["action"],
        "commit": request["args"][0 if request["action"] == "validate" else 1],
        "task": request.get("task", "manual"),
        "created_at": time.time(),
    }
    save(directory / "status.json", record)
    result = subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--quiet",
            "--collect",
            "--unit=devagent-operation-" + identifier,
            "--property=RuntimeMaxSec=7200",
            "--property=TimeoutStopSec=30",
            "--property=KillMode=control-group",
            "--property=UMask=0077",
            "--property=CPUQuota=200%",
            "--property=MemoryMax=7G",
            "--property=TasksMax=256",
            "--property=StandardOutput=null",
            "--property=StandardError=null",
            "/usr/bin/python3",
            "-I",
            BROKER,
            "--worker",
            identifier,
        ],
        capture_output=True,
        timeout=15,
    )
    if result.returncode:
        record.update(state="failed", finished_at=time.time())
        save(directory / "status.json", record)
        raise RuntimeError("protected operation could not start")
    return record


def redact(value, config):
    # Return only a bounded diagnostic excerpt, never root files or arbitrary paths.
    for key in ("release_secret", "policy_secret", "validator_token", "pve_key", "stage_key"):
        if key in config and Path(config[key]).is_file():
            secret = Path(config[key]).read_text().strip()
            if secret:
                value = value.replace(secret, "[REDACTED]")
    for path in (
        Path("/var/lib/devagent/.config/devagent/forgejo-token"),
        Path("/var/lib/devagent/.local/share/opencode/auth.json"),
    ):
        if path.is_file():
            raw = path.read_text().strip()
            if path.suffix == ".json":
                for provider in json.loads(raw).values():
                    if provider.get("key"):
                        value = value.replace(provider["key"], "[REDACTED]")
            elif raw:
                value = value.replace(raw, "[REDACTED]")
    value = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[REDACTED]", value, flags=re.S
    )
    value = re.sub(r"(?i)(?:bearer|token)\s+[A-Za-z0-9._~+/=-]{16,}", "[REDACTED]", value)
    return value


def job_logs(identifier, config):
    directory = job_path(identifier)
    job_status(identifier)
    log = directory / "runner.log"
    if (directory / "validation.json").is_file():
        reference = json.loads((directory / "validation.json").read_text())
        if not re.fullmatch("[a-f0-9]{40}-[a-f0-9]{32}", reference["directory"]):
            raise ValueError("invalid validation reference")
        log = ROOT / "validation" / reference["directory"] / "commands.log"
    if not log.is_file():
        return {"id": identifier, "tail": "No command output yet"}
    # Redact before truncating; a truncation must not split a known credential.
    with log.open("rb") as stream:
        stream.seek(max(0, log.stat().st_size - 65536))
        tail = stream.read(65536).decode("utf-8", errors="replace")
    return {"id": identifier, "tail": redact(tail, config)[-12000:]}


def worker(identifier):
    if os.geteuid() != 0:
        raise ValueError("root worker required")
    directory = job_path(identifier)
    request = json.loads((directory / "request.json").read_text())
    request_valid(request)
    if request["action"] not in ("validate", "deploy"):
        raise ValueError("invalid worker action")
    record = json.loads((directory / "status.json").read_text())
    if record["state"] != "queued":
        raise ValueError("operation already started")
    os.environ.clear()
    os.environ.update(
        PATH="/usr/local/bin:/usr/bin:/bin", HOME="/root", LANG="C.UTF-8", DEVAGENT_OPERATION=identifier
    )
    with (ROOT / "execution.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        record.update(state="running", started_at=time.time())
        save(directory / "status.json", record)
        try:
            with (directory / "runner.log").open("wb") as log:
                completed = subprocess.run(
                    [RUNNER],
                    input=json.dumps(request),
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=log,
                    timeout=7100,
                )
            if completed.returncode:
                raise RuntimeError("protected preflight/deployment failed")
            result = json.loads(completed.stdout)
            # Full output stays protected; caller sees deterministic state/evidence fields.
            save(directory / "result.json", result)
            record.update(
                state="completed",
                result=result.get("state", "validated"),
                verification=result.get("verification", {}),
            )
        except Exception:
            record.update(state="failed")
            if request["action"] == "deploy":
                audit = ROOT / "deployments" / identifier / "audit.json"
                if audit.is_file():
                    outcome = json.loads(audit.read_text())
                    record["recovery_required"] = bool(outcome.get("recovery_required"))
                else:
                    record["recovery_required"] = True
        finally:
            record["finished_at"] = time.time()
            save(directory / "status.json", record)
            with (ROOT / "operations.jsonl").open("a") as log:
                log.write(json.dumps(record) + "\n")


def request_valid(request):
    if not isinstance(request, dict):
        raise ValueError("object request required")
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
    elif request["action"] in ("operation", "operation-logs", "publication-recovery"):
        if len(args) != 1:
            raise ValueError("one operation identifier required")
        job_path(args[0])
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
    if request["action"] in ("operation", "operation-logs", "publication-recovery"):
        with (ROOT / "operation.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = (
                job_status(request["args"][0]) if request["action"] == "operation"
                else job_logs(request["args"][0], config) if request["action"] == "operation-logs"
                else publication_recovery(request["args"][0])
            )
        print(json.dumps(result))
        return
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
                result = submit(request)
                record["operation_id"] = result["id"]
            record["result"] = "completed" if request["action"] == "infrastructure" else "submitted"
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
        if len(sys.argv) == 3 and sys.argv[1] == "--worker":
            worker(sys.argv[2])
        elif len(sys.argv) == 1:
            main()
        else:
            raise ValueError("unsupported invocation")
    except (ValueError, KeyError, RuntimeError, OSError, subprocess.TimeoutExpired):
        print("Privileged operation rejected or failed", file=sys.stderr)
        sys.exit(2)
