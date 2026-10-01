#!/usr/bin/python3 -I
"""Root-owned deterministic preflight. Project tests/build execute as devagent."""

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

ROOT = Path("/var/lib/devagent-privileged")


def user_command(argv, cwd, log):
    # A transient service contains project code and its descendants in a bounded cgroup.
    command = [
        "/usr/bin/systemd-run",
        "--quiet",
        "--wait",
        "--pipe",
        "--collect",
        "--unit=devagent-preflight-" + uuid4().hex,
        "--uid=devagent",
        "--gid=devagent",
        "--property=CPUQuota=200%",
        "--property=MemoryMax=5G",
        "--property=TasksMax=256",
        "--property=RuntimeMaxSec=1200",
        "--property=WorkingDirectory=" + str(cwd),
        "--setenv=HOME=/var/lib/devagent",
        "--setenv=UV_PYTHON_INSTALL_DIR=/opt/xeonolab/python",
        "--setenv=CHROME_BIN=/opt/devagent/browser/chrome/chrome",
        "--setenv=GIT_TERMINAL_PROMPT=0",
        "--setenv=PATH=/usr/local/bin:/usr/bin:/bin",
        *argv,
    ]
    with log.open("ab") as output:
        result = subprocess.run(command, stdout=output, stderr=output, timeout=1250)
    if result.returncode:
        raise RuntimeError("preflight command failed; inspect protected validation log")


def validate(sha):
    if not re.fullmatch("[a-f0-9]{40}", sha):
        raise ValueError("exact validation commit required")
    task = uuid4().hex
    report_dir = ROOT / "validation" / (sha + "-" + task)
    report_dir.mkdir(parents=True, mode=0o700)
    worktree = Path("/var/lib/devagent/worktrees") / ("validate-" + task)
    checkout = Path("/var/lib/devagent/checkout")
    log = report_dir / "commands.log"
    report = {"commit": sha, "id": task, "at": time.time(), "state": "running", "checks": []}
    try:
        user_command(["/usr/bin/git", "fetch", "--all", "--prune"], checkout, log)
        user_command(["/usr/bin/git", "cat-file", "-e", sha + "^{commit}"], checkout, log)
        user_command(["/usr/bin/git", "worktree", "add", "--detach", str(worktree), sha], checkout, log)
        user_command(["/usr/local/bin/uv", "sync", "--locked", "--python", "3.12"], worktree, log)
        user_command(["/usr/bin/mkdir", "-p", "runtime"], worktree, log)
        commands = [
            ("ruff", ["/usr/local/bin/uv", "run", "ruff", "check", "xeonolab", "tests", "scripts"]),
            ("pytest", ["/usr/local/bin/uv", "run", "pytest", "--basetemp", "runtime/validation-test-tmp"]),
            ("service_smoke", ["/usr/local/bin/uv", "run", "python", "scripts/smoke_services.py"]),
            ("failure_paths", ["/usr/local/bin/uv", "run", "python", "scripts/verify_failure_injection.py"]),
            ("openapi", ["/usr/local/bin/uv", "run", "python", "scripts/export_openapi.py"]),
            ("openapi_clean", ["/usr/bin/git", "diff", "--exit-code", "--", "docs/api"]),
        ]
        for name, argv in commands:
            user_command(argv, worktree, log)
            report["checks"].append(name)
        frontend = worktree / "apps/desktop-web"
        lock_hash = hashlib.sha256((frontend / "package-lock.json").read_bytes()).hexdigest()
        cache = Path("/opt/devagent/frontend-cache") / lock_hash
        if cache.is_dir():
            # Copy writable dependencies for Vite/TS caches; the shared seed stays root-owned.
            user_command(["/usr/bin/cp", "-a", str(cache / "node_modules"), str(frontend)], worktree, log)
            user_command(["/usr/bin/chmod", "-R", "u+w", str(frontend / "node_modules")], worktree, log)
        else:
            user_command(["/usr/local/bin/npm", "ci"], frontend, log)
        for name, script in [
            ("frontend_unit", "test"),
            ("browser_smoke", "test:browser-smoke"),
            ("browser_regression", "test:browser-regression"),
            ("frontend_build", "build"),
        ]:
            user_command(["/usr/local/bin/npm", "run", script], frontend, log)
            report["checks"].append(name)
        user_command(["/usr/bin/git", "diff", "--exit-code"], worktree, log)
        report["state"] = "passed"
        report["worktree"] = str(worktree)
        # Build artifacts are captured as opaque bytes under a root-owned directory.
        artifact = report_dir / "frontend.tar"
        with artifact.open("wb") as output:
            subprocess.run(
                [
                    "/usr/bin/sudo",
                    "-n",
                    "-H",
                    "-u",
                    "devagent",
                    "/usr/bin/tar",
                    "-cf",
                    "-",
                    "-C",
                    str(worktree),
                    "apps/desktop-web/dist",
                ],
                stdout=output,
                check=True,
            )
        report["frontend_digest"] = hashlib.file_digest(artifact.open("rb"), "sha256").hexdigest()
    finally:
        report["finished_at"] = time.time()
        (report_dir / "report.json").write_text(json.dumps(report, indent=2))
        if report["state"] != "passed":
            report["state"] = "failed"
            (report_dir / "report.json").write_text(json.dumps(report, indent=2))
    return report, report_dir


if __name__ == "__main__":
    # Installed only for root broker execution, not a user sudo entry point.
    os.umask(0o077)
    if os.geteuid() != 0:
        raise SystemExit(2)
    report, directory = validate(sys.argv[1])
    print(json.dumps({"report": report, "directory": str(directory)}))
