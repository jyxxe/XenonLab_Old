#!/usr/bin/python3 -I
"""Operator-only abort of a failed preflight. Never activates code or edits the ledger."""

import argparse
import base64
import fcntl
import hashlib
import importlib.machinery
import importlib.util
import json
import math
import os
import re
import stat
import subprocess
import sys
import time
import urllib.request
from itertools import islice
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path("/var/lib/devagent-privileged")
CONFIG = Path("/etc/devagent/secrets/privileged.json")
RUNNER = Path("/usr/local/libexec/devagent-deploy")
REPOSITORY = "xenon-admin/XeonoLab"
TARGETS = ("101", "107", "104", "111", "106")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def remaining(deadline, maximum=15):
    seconds = deadline - time.monotonic()
    require(seconds > 0, "operator check deadline exceeded")
    return min(seconds, maximum)


class BoundedOpener:
    def __init__(self, opener, deadline):
        self.opener, self.deadline = opener, deadline

    def open(self, request, timeout=None):
        return self.opener.open(request, timeout=remaining(self.deadline))


def stage_probe(config, node, command, deadline):
    require(node in TARGETS and command in ("devagent-stage status", "devagent-stage publication-recovery"),
            "fixed recovery probe required")
    require(command != "devagent-stage publication-recovery" or node == "106", "gateway probe target required")
    result = subprocess.run(
        ["/usr/bin/ssh", "-i", config["stage_key"], "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes",
         "-o", "UserKnownHostsFile=" + config["known_hosts"], "-o", "ConnectTimeout=5",
         "devagent-stage@192.168.0." + node, command],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=remaining(deadline, 20),
    )
    require(result.returncode == 0 and len(result.stdout) <= 65536, "target recovery probe failed")
    value = json.loads(result.stdout)
    require(isinstance(value, dict), "target proof object required")
    return value


def owned_directory(path):
    info = path.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
            "unsafe operator directory")


def read_json(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as file:
        info = os.fstat(file.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and info.st_size <= 65536, "unsafe native evidence")
        raw = file.read(65537)
    require(len(raw) <= 65536, "native evidence exceeds size limit")
    value = json.loads(raw)
    require(isinstance(value, dict), "native object evidence required")
    return value, hashlib.sha256(raw).hexdigest()


def atomic_json(path, value, replace=False):
    owned_directory(path.parent)
    temporary = path.parent / ("." + uuid4().hex + ".tmp")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as file:
            json.dump(value, file, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        if replace:
            os.replace(temporary, path)
        else:
            os.link(temporary, path)  # No overwrite, including after an uncertain prior attempt.
            temporary.unlink()
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def validate_request(value):
    fields = {"number", "head", "commit", "operation_id", "baseline_commit",
              "baseline_operation_id", "reason"}
    require(isinstance(value, dict) and set(value) == fields, "exact operator request required")
    require(type(value["number"]) is int and 0 < value["number"] <= 10000000, "invalid PR number")
    for key in ("head", "commit", "baseline_commit"):
        require(isinstance(value[key], str) and re.fullmatch("[a-f0-9]{40}", value[key]),
                "exact commit required")
    for key in ("operation_id", "baseline_operation_id"):
        require(isinstance(value[key], str) and re.fullmatch("[a-f0-9]{32}", value[key]),
                "exact operation identifier required")
    require(value["commit"] != value["baseline_commit"]
            and value["operation_id"] != value["baseline_operation_id"], "distinct baseline required")
    require(isinstance(value["reason"], str) and 1 <= len(value["reason"]) <= 512
            and not any(ord(c) < 32 for c in value["reason"]), "bounded operator reason required")
    return value


def load_runner():
    info = RUNNER.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022,
            "reviewed root-owned deployment runner required")
    loader = importlib.machinery.SourceFileLoader("trusted_deployment", str(RUNNER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def native_job(identifier):
    directory = ROOT / "jobs" / identifier
    audit_directory = ROOT / "deployments" / identifier
    for path in (ROOT, ROOT / "jobs", directory, ROOT / "deployments", audit_directory):
        owned_directory(path)
    job, job_hash = read_json(directory / "status.json")
    request, _ = read_json(directory / "request.json")
    audit, audit_hash = read_json(audit_directory / "audit.json")
    require(job.get("id") == identifier and job.get("action") == "deploy"
            and request.get("action") == "deploy", "native deploy job required")
    require(request.get("args") == [REPOSITORY, job.get("commit"), "production"]
            and audit.get("repository") == REPOSITORY and audit.get("environment") == "production"
            and audit.get("commit") == job.get("commit"), "native deployment binding mismatch")
    return job, audit, job_hash, audit_hash


def check_native(request, deadline=None):
    deadline = time.monotonic() + 120 if deadline is None else deadline
    failed, audit, job_hash, audit_hash = native_job(request["operation_id"])
    require(failed.get("state") == "failed" and failed.get("commit") == request["commit"]
            and failed.get("recovery_required") is False, "terminal safe failed job required")
    require(audit.get("state") == "failed" and audit.get("state_before_failure") == "preflight"
            and audit.get("previous") == {} and audit.get("verification") == {}
            and not audit.get("recovery_required")
            and not any(key in audit for key in ("release_id", "lease", "artifact", "deployment")),
            "only pre-activation preflight failures can be aborted")
    baseline, baseline_audit, _, _ = native_job(request["baseline_operation_id"])
    require(baseline.get("state") == "completed" and baseline.get("result") == "completed"
            and baseline.get("commit") == request["baseline_commit"]
            and baseline_audit.get("state") == "completed"
            and not baseline.get("recovery_required") and not baseline_audit.get("recovery_required"),
            "completed native baseline required")
    verification = baseline.get("verification")
    require(isinstance(verification, dict) and set(verification) == set(TARGETS) | {"gateway"},
            "all native baseline targets and gateway required")
    for node in TARGETS:
        require(isinstance(verification[node], dict)
                and verification[node].get("expected") == request["baseline_commit"]
                and verification[node].get("actual") == request["baseline_commit"]
                and verification[node].get("ok") is True,
                "baseline target was not verified")
    require(isinstance(verification["gateway"], dict) and verification["gateway"].get("ok") is True,
            "baseline gateway was not verified")
    require(type(baseline.get("finished_at")) in (int, float)
            and type(failed.get("created_at")) in (int, float)
            and baseline["finished_at"] < failed["created_at"], "baseline chronology is invalid")
    directories = list(islice((ROOT / "jobs").iterdir(), 1025))
    require(len(directories) <= 1024, "native job scan exceeds bounded recovery limit")
    for directory in directories:
        remaining(deadline)
        require(re.fullmatch("[a-f0-9]{32}", directory.name), "unrecognized native job directory")
        owned_directory(directory)
        job, _ = read_json(directory / "status.json")
        require(job.get("id") == directory.name and job.get("state") in ("completed", "failed", "interrupted"),
                "protected job is active or unresolved")
        require(not (job.get("action") == "deploy" and job.get("recovery_required")),
                "another deployment needs recovery")
    result = subprocess.run(["/usr/bin/systemctl", "is-active", "--quiet"]
                            + ["devagent-operation-" + directory.name for directory in directories],
                            capture_output=True, timeout=remaining(deadline, 20))
    require(result.returncode in (3, 4), "protected operation service is active or unknown")
    return {"native_job_sha256": job_hash, "native_audit_sha256": audit_hash}, baseline_audit


def forgejo_get(config, path, deadline):
    import ssl

    origin = urlsplit(config["forgejo_url"])
    require(origin.scheme == "https" and origin.netloc == "192.168.0.101:9445"
            and origin.path in ("", "/") and not origin.query and not origin.fragment,
            "fixed TLS Forgejo origin required before sending credentials")
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=config["ca_file"])),
    )
    request = urllib.request.Request(config["forgejo_url"].rstrip("/") + "/api/v1/repos/" + REPOSITORY + path,
                                     headers={"Authorization": "token " + Path(config["validator_token"]).read_text().strip()})
    with opener.open(request, timeout=remaining(deadline)) as response:
        return json.loads(response.read(1024 * 1024))


def check_forgejo(config, request, deadline=None):
    deadline = time.monotonic() + 45 if deadline is None else deadline
    pr = forgejo_get(config, "/pulls/" + str(request["number"]), deadline)
    require(pr.get("merged") is True and pr.get("state") == "closed"
            and pr.get("head", {}).get("sha") == request["head"]
            and pr.get("merge_commit_sha") == request["commit"]
            and pr.get("base", {}).get("ref") == "feature/platform-v1"
            and pr.get("base", {}).get("repo", {}).get("full_name") == REPOSITORY,
            "Forgejo merged publication tuple mismatch")
    branch = forgejo_get(config, "/branches/feature%2Fplatform-v1", deadline)
    require(branch.get("commit", {}).get("id") == request["commit"], "integration branch changed")


def free_lease(lease):
    return (isinstance(lease, dict) and type(lease.get("epoch")) is int and lease["epoch"] >= 0
            and all(key in lease and lease[key] is None for key in ("id", "holder", "release_id", "expires")))


def owned_lease(actual, expected, holder, release_id, previous_epoch):
    require(isinstance(actual, dict) and isinstance(expected, dict), "lease object required")
    identifier = expected.get("lease_id")
    require(isinstance(identifier, str) and re.fullmatch("[a-f0-9-]{36}", identifier)
            and actual.get("id") == identifier and actual.get("holder") == holder
            and actual.get("release_id") == release_id
            and type(actual.get("epoch")) is int and actual["epoch"] == previous_epoch + 1
            and expected.get("epoch") == actual["epoch"]
            and type(actual.get("expires")) in (int, float) and math.isfinite(actual["expires"])
            and actual["expires"] > time.time(), "operator lease binding mismatch")


def abort(request, config, runner):
    evidence, baseline_audit = check_native(request)
    check_forgejo(config, request)
    gateway = runner.Gateway(config)
    bounded_opener = BoundedOpener(gateway.opener, time.monotonic() + 90)
    gateway.opener = bounded_opener
    before = gateway.api("GET", "/api/v1/deploy/lease")
    require(free_lease(before), "deployment lease is held; no recovery attempted")
    release_id = baseline_audit.get("release_id")
    require(isinstance(release_id, str) and re.fullmatch("[a-f0-9-]{36}", release_id),
            "baseline release is missing")
    releases = gateway.api("GET", "/api/v1/releases")
    require(isinstance(releases, list), "release list required")
    matches = [row for row in releases if row.get("id") == release_id]
    require(len(matches) == 1 and matches[0].get("approved") == 1
            and json.loads(matches[0]["manifest"]).get("commit") == request["baseline_commit"],
            "approved baseline release mismatch")
    token = gateway.token()
    claims = json.loads(base64.urlsafe_b64decode(token.split(".")[1] + "=="))
    holder = claims.get("sub")
    require(isinstance(holder, str) and re.fullmatch("[a-f0-9-]{36}", holder), "native operator identity required")
    attempt_dir = ROOT / "publication-recovery-attempts"
    receipt_dir = ROOT / "publication-recoveries"
    for directory in (attempt_dir, receipt_dir):
        directory.mkdir(mode=0o700, exist_ok=True)
        owned_directory(directory)
    sync_directory(ROOT)  # Persist both parent entries before acquiring a lease.
    attempt_path = attempt_dir / (request["operation_id"] + ".json")
    receipt_path = receipt_dir / (request["operation_id"] + ".json")
    require(not receipt_path.exists() and not receipt_path.is_symlink(), "recovery receipt already exists")
    attempt = {"request": request, "at": time.time(), "state": "acquiring", "prior_epoch": before["epoch"]}
    atomic_json(attempt_path, attempt)
    lease = None
    checked = False
    try:
        bounded_opener.deadline = time.monotonic() + 150
        acquired = gateway.api("POST", "/api/v1/deploy/lease/acquire",
                               {"agent_id": holder, "release_id": release_id, "ttl_seconds": 300})
        require(isinstance(acquired, dict) and isinstance(acquired.get("lease_id"), str)
                and re.fullmatch("[a-f0-9-]{36}", acquired["lease_id"])
                and type(acquired.get("epoch")) is int and acquired["epoch"] == before["epoch"] + 1,
                "operator acquire acknowledgement is malformed")
        lease = acquired
        attempt.update(state="acquired", lease_ack=acquired)
        atomic_json(attempt_path, attempt, replace=True)
        live = gateway.api("GET", "/api/v1/deploy/lease")
        owned_lease(live, acquired, holder, release_id, before["epoch"])
        attempt.update(state="checking", lease=live)
        atomic_json(attempt_path, attempt, replace=True)
        targets = {}
        for node in TARGETS:
            target = stage_probe(config, node, "devagent-stage status", bounded_opener.deadline)
            require(target.get("current") == request["baseline_commit"] and target.get("ready") is True,
                    "live production differs from verified baseline")
            targets[node] = {"commit": target["current"], "ready": True}
        proof = stage_probe(config, "106", "devagent-stage publication-recovery", bounded_opener.deadline)
        require(proof.get("gateway_commit") == request["baseline_commit"]
                and proof.get("runtime_commit") == request["baseline_commit"]
                and proof.get("ready") is True and type(proof.get("unresolved_deployments")) is int
                and proof["unresolved_deployments"] == 0, "gateway recovery proof is unresolved")
        owned_lease(proof.get("lease"), lease, holder, release_id, before["epoch"])
        current_evidence, _ = check_native(request, bounded_opener.deadline)
        require(current_evidence == evidence, "native evidence changed during checks")
        check_forgejo(config, request, bounded_opener.deadline)
        attempt.update(state="verified", targets=targets, gateway=proof, evidence=evidence)
        atomic_json(attempt_path, attempt, replace=True)
        checked = True
    finally:
        if lease is not None:
            bounded_opener.deadline = time.monotonic() + 90
            # No executor is invoked here. The Gateway still refuses release if anything is unresolved.
            live = gateway.api("GET", "/api/v1/deploy/lease")
            owned_lease(live, lease, holder, release_id, before["epoch"])
            released = gateway.api("POST", "/api/v1/deploy/lease/" + lease["lease_id"] + "/release")
            require(released.get("released") is True, "operator lease release was not confirmed")
            after = gateway.api("GET", "/api/v1/deploy/lease")
            require(free_lease(after) and after["epoch"] == lease["epoch"], "lease release readback differs")
            attempt.update(state="released_verified" if checked else "released_failed_checks", released_at=time.time())
            atomic_json(attempt_path, attempt, replace=True)
    require(checked, "recovery checks did not finish")
    receipt = {"receipt_id": str(uuid4()), "state": "operator_aborted", "operation_id": request["operation_id"],
               "number": request["number"], "head": request["head"], "commit": request["commit"],
               "baseline_commit": request["baseline_commit"],
               "evidence": dict(evidence, targets_verified=True, gateway_unresolved=False, lease_active=False)}
    atomic_json(receipt_path, receipt)
    attempt.update(state="operator_aborted", receipt_id=receipt["receipt_id"], finished_at=time.time())
    atomic_json(attempt_path, attempt, replace=True)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abort-preflight", action="store_true")
    args = parser.parse_args()
    if not args.abort_preflight:
        print(json.dumps({"action": "plan", "writes": False, "network": False,
                          "operator_only": True, "requires": "exact merged PR and failed preflight operation"}))
        return
    require(os.geteuid() == 0 and os.getenv("SUDO_USER") in (None, "root", "xenon"),
            "operator root caller required; DevAgent cannot abort publications")
    request = validate_request(json.loads(sys.stdin.read(16000)))
    os.umask(0o077)
    owned_directory(ROOT)
    config, _ = read_json(CONFIG)
    runner = load_runner()
    descriptor = os.open(ROOT / "operation.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        info = os.fstat(lock.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o077,
                "unsafe operation lock")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        print(json.dumps(abort(request, config, runner)))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("Operator publication recovery rejected; inspect root attempt/evidence. No deployment was retried.",
              file=sys.stderr)
        sys.exit(2)
