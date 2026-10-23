"""Lean sandbox verifier service (plan.md §40, issue #28).

An independent worker that elaborates one Lean source file inside a bounded
child process. The service owns the sandbox contract:

- Toolchains are resolved only from a trusted, root-owned manifest file
  (``LEAN_TOOLCHAINS_FILE``): the manifest and every toolchain binary must be
  root-owned regular files that are not group/other writable, and each entry
  pins the binary's SHA-256. Request input can name a toolchain but never
  supplies a path.
- Network/filesystem isolation is enforced by the reviewed systemd unit
  (``infra/lean-verifier/lean-verifier.service``: PrivateNetwork, PrivateTmp,
  ProtectSystem=strict, NoNewPrivileges, RestrictAddressFamilies=AF_UNIX ...).
  Readiness requires observable isolation: the worker's network and mount
  namespaces must differ from PID 1's, so a forgotten unit keeps the service
  unavailable instead of silently running unsandboxed.
- The child runs with a stripped environment (no credentials, no proxy), its
  own session/process group, CPU/address-space/file-size/process-count
  rlimits, and walltime enforced by killing the whole process group.
- Sync and queued submissions share one bounded admission pool; the queued set
  and retained source/job storage are capped with terminal-history retention.
  stdout/stderr are collected with a fixed in-memory cap while the process runs.
- Locked-dependency verification is NOT supported: results report
  ``dependency_lock_checked: false`` and the deployment notes say so; the
  verifier never claims a lockfile was checked.
- Without a trusted toolchain and observable isolation the service fails
  closed with ``VERIFIER_UNAVAILABLE`` instead of pretending to verify.

Durable jobs live in ``DATA_DIR/lean-verifier.sqlite`` and are owner-scoped:
status/cancel are only available to the principal that created the job.
Queued/running jobs are re-queued on restart because verification is
idempotent. Cancellation kills a running process group and marks queued jobs
cancelled before they run.
"""

import hashlib
import json
import logging
import os
import re
import resource
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, response, scope, transaction

logger = logging.getLogger("xenolab.lean-verifier")

SERVICE = "lean-verifier"
SOURCE_LIMIT = 256 * 1024
OUTPUT_LIMIT = 16 * 1024
READ_CHUNK = 64 * 1024
MAX_TIMEOUT = 120
MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
FILE_LIMIT_BYTES = 64 * 1024 * 1024
PROCESS_LIMIT = 64
MAX_QUEUED = int(os.getenv("LEAN_MAX_QUEUED", "64"))
MAX_CONCURRENT = max(1, int(os.getenv("LEAN_MAX_CONCURRENT", "2")))
JOB_HISTORY = int(os.getenv("LEAN_JOB_HISTORY", "200"))
TOOLCHAIN_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
AXIOM_DECLARATION = re.compile(r"^\s*axiom\s+([A-Za-z_][A-Za-z0-9_!'?]+)", re.MULTILINE)
PRINT_AXIOMS = re.compile(r"depends on axioms\s*:?\s*\[([^\]]*)\]")

TERMINAL = ("verified", "failed", "timeout", "cancelled", "runner_error")
# Test seam: tests may replace _stat to simulate root-owned files/namespaces.
_stat = os.stat


class VerifyRequest(BaseModel):
    source: str = Field(min_length=1, max_length=SOURCE_LIMIT)
    toolchain: str = Field(default="lean4", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
    timeout_seconds: int = Field(default=60, ge=1, le=MAX_TIMEOUT)
    network: bool = False
    wait: bool = True


def _trusted_regular_file(path):
    """Root-owned, non-symlink regular file that group/other cannot write."""
    try:
        info = _stat(path)
    except OSError:
        return None
    if info.st_uid != 0 or not stat.S_ISREG(info.st_mode):
        return None
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return None
    return info


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_toolchains(path):
    """Read the trusted toolchain manifest; any problem fails closed."""
    if not path or _trusted_regular_file(path) is None:
        return {}
    try:
        manifest = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    entries = manifest.get("toolchains") if isinstance(manifest, dict) else None
    if not isinstance(entries, list):
        return {}
    toolchains = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name, candidate = entry.get("name"), entry.get("path")
        expected = entry.get("sha256")
        if not isinstance(name, str) or not TOOLCHAIN_NAME.fullmatch(name):
            continue
        if not isinstance(candidate, str) or not candidate.startswith("/"):
            continue
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            continue
        if _trusted_regular_file(candidate) is None:
            continue
        try:
            if not os.access(candidate, os.X_OK):
                continue
            if _sha256(candidate) != expected:
                continue
            toolchains[name] = str(Path(candidate).resolve())
        except OSError:
            continue
    return toolchains


def _namespace_isolated():
    """True when the worker runs in private network AND mount namespaces."""
    for kind in ("net", "mnt"):
        try:
            ours = _stat(f"/proc/self/ns/{kind}")
            init = _stat(f"/proc/1/ns/{kind}")
        except OSError:
            return False
        if ours.st_ino == init.st_ino:
            return False
    return True


def sandbox_status():
    return {"isolated": _namespace_isolated(), "detail": "network+mount namespace isolation"}


def _truncate(text):
    if len(text) > OUTPUT_LIMIT:
        return text[:OUTPUT_LIMIT], True
    return text, False


def extract_axioms(source, stdout):
    """Report the axioms we can honestly observe: source declarations plus
    ``#print axioms`` output. An empty list means "none observed"; callers
    that need the soundness distinction should include ``#print axioms``
    lines in the source."""
    axioms = set(AXIOM_DECLARATION.findall(source))
    for match in PRINT_AXIOMS.finditer(stdout):
        for name in match.group(1).split(","):
            name = name.strip().strip("'\"")
            if name:
                axioms.add(name)
    return sorted(axioms)


def _child_limits(cpu_seconds):
    def apply():
        # Session/process-group creation is handled by start_new_session;
        # a second setsid() here would fail with EPERM.
        os.umask(0o077)
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_LIMIT_BYTES, FILE_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_NPROC, (PROCESS_LIMIT, PROCESS_LIMIT))

    return apply


def _child_environment(toolchain_dir, workdir):
    # Deliberately absent: every proxy variable, credentials and anything
    # inherited from the service environment.
    return {
        "PATH": f"{toolchain_dir}:/usr/bin:/bin",
        "HOME": str(workdir),
        "TMPDIR": str(workdir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }


def _kill_group(process):
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def _read_bounded(stream, sink):
    """Collect at most OUTPUT_LIMIT+1 bytes while the process runs."""
    kept = 0
    while True:
        chunk = stream.read(READ_CHUNK)
        if not chunk:
            return
        if kept < OUTPUT_LIMIT + 1:
            sink.append(chunk[: OUTPUT_LIMIT + 1 - kept])
            kept += len(chunk[: OUTPUT_LIMIT + 1 - kept])


def default_runner(toolchain_path, source_path, workdir, timeout_seconds, register=None):
    """Run one bounded verification. Returns an honest outcome dict and never
    raises for expected outcomes (timeout, nonzero exit)."""
    started = time.monotonic()
    try:
        process = subprocess.Popen(
            [str(toolchain_path), str(source_path)],
            cwd=workdir,
            env=_child_environment(str(Path(toolchain_path).parent), workdir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            preexec_fn=_child_limits(max(1, timeout_seconds)),
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning("lean.runner.start_failed %s", exc, exc_info=True)
        return {
            "status": "runner_error",
            "exit_code": None,
            "stdout": "",
            "stderr": f"runner failed to start the toolchain: {type(exc).__name__}",
            "walltime_ms": int((time.monotonic() - started) * 1000),
            "axioms": [],
            "dependency_lock_checked": False,
        }
    if register is not None:
        register(process)
    out_chunks, err_chunks = [], []
    readers = [
        threading.Thread(target=_read_bounded, args=(process.stdout, out_chunks), daemon=True),
        threading.Thread(target=_read_bounded, args=(process.stderr, err_chunks), daemon=True),
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        try:
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_group(process)
            exit_code = process.wait()
    finally:
        for reader in readers:
            reader.join(timeout=5)
        if register is not None:
            register(None)
    walltime = int((time.monotonic() - started) * 1000)
    stdout_text, stdout_truncated = _truncate(b"".join(out_chunks).decode("utf-8", "replace"))
    stderr_text, stderr_truncated = _truncate(b"".join(err_chunks).decode("utf-8", "replace"))
    status = "timeout" if timed_out else ("verified" if exit_code == 0 else "failed")
    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "walltime_ms": walltime,
        "axioms": extract_axioms(Path(source_path).read_text(errors="replace"), stdout_text),
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "dependency_lock_checked": False,
    }


def create_app(settings=None, runner=None):
    app = base_app(SERVICE, settings)
    settings = app.state.settings
    db_path = settings.data_dir / "lean-verifier.sqlite"
    jobs_root = settings.data_dir / "lean-jobs"
    jobs_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    runner = runner or default_runner
    pool = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="lean-verify")
    admission = threading.BoundedSemaphore(MAX_CONCURRENT)
    running_lock = threading.Lock()
    running = {}

    def log_event(event, *, level=logging.INFO, **fields):
        logger.log(level, json.dumps({"event": f"lean.{event}", "source": SERVICE, **fields}))

    def count(outcome_status):
        app.state.metrics.counter(
            "leanverifier_verifications_total",
            help="Lean verification attempts by outcome.",
            status=outcome_status,
        )

    with transaction(db_path) as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS lean_jobs(
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, toolchain TEXT NOT NULL,
                timeout INTEGER NOT NULL, source_path TEXT NOT NULL, status TEXT NOT NULL,
                error TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created REAL NOT NULL, updated REAL NOT NULL)"""
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(lean_jobs)")}
        if "owner" not in columns:
            db.execute("ALTER TABLE lean_jobs ADD COLUMN owner TEXT NOT NULL DEFAULT ''")

    def toolchain_path(name):
        resolved = load_toolchains(os.getenv("LEAN_TOOLCHAINS_FILE", "")).get(name)
        if not resolved:
            raise APIError(503, "VERIFIER_UNAVAILABLE", f"no trusted executable toolchain named {name!r} is configured")
        if not sandbox_status()["isolated"]:
            raise APIError(
                503, "VERIFIER_UNAVAILABLE",
                "verification sandbox is not active: network/mount namespace isolation is required",
            )
        return resolved

    def job_view(row):
        result = json.loads(row["result"]) if row["result"] else None
        return {
            "id": row["id"],
            "toolchain": row["toolchain"],
            "timeout_seconds": row["timeout"],
            "status": row["status"],
            "error": row["error"] or None,
            "result": result,
            "cancel_requested": bool(row["cancel_requested"]),
            "created_at": row["created"],
            "updated_at": row["updated"],
        }

    def fetch_job(job_id):
        with connect(db_path) as db:
            return db.execute("SELECT * FROM lean_jobs WHERE id=?", (job_id,)).fetchone()

    def owned_job(job_id, actor):
        row = fetch_job(job_id)
        if row is None:
            raise APIError(404, "JOB_NOT_FOUND", "no such verification job")
        if row["owner"] != actor.sub:
            raise APIError(403, "JOB_FORBIDDEN", "verification job belongs to another caller")
        return row

    def update_job(job_id, **fields):
        fields["updated"] = time.time()
        with transaction(db_path) as db:
            db.execute(
                "UPDATE lean_jobs SET " + ",".join(f"{name}=?" for name in fields) + " WHERE id=?",
                (*fields.values(), job_id),
            )

    def queued_count():
        with connect(db_path) as db:
            return db.execute(
                "SELECT COUNT(*) FROM lean_jobs WHERE status IN ('queued','running')"
            ).fetchone()[0]

    def prune_jobs():
        with transaction(db_path) as db:
            stale = db.execute(
                "SELECT id,source_path FROM lean_jobs WHERE status NOT IN ('queued','running') "
                "AND id NOT IN (SELECT id FROM lean_jobs WHERE status NOT IN ('queued','running') "
                "ORDER BY created DESC LIMIT ?)",
                (JOB_HISTORY,),
            ).fetchall()
            for row in stale:
                db.execute("DELETE FROM lean_jobs WHERE id=?", (row["id"],))
        for row in stale:
            shutil.rmtree(Path(row["source_path"]).parent, ignore_errors=True)

    def register_process(job_id):
        def register(process):
            with running_lock:
                if process is None:
                    running.pop(job_id, None)
                else:
                    running[job_id] = process
            if process is not None:
                # A cancel can arrive between the pre-run flag check and process
                # registration; kill immediately instead of waiting out walltime.
                with connect(db_path) as db:
                    row = db.execute("SELECT cancel_requested FROM lean_jobs WHERE id=?", (job_id,)).fetchone()
                if row and row["cancel_requested"]:
                    _kill_group(process)

        return register

    def execute(job_id, toolchain, source_path, timeout_seconds):
        workdir = Path(tempfile.mkdtemp(prefix="lean-", dir=jobs_root))
        try:
            with connect(db_path) as db:
                row = db.execute("SELECT cancel_requested FROM lean_jobs WHERE id=?", (job_id,)).fetchone()
            if row and row["cancel_requested"]:
                update_job(job_id, status="cancelled", error="")
                return
            with admission:
                with connect(db_path) as db:
                    row = db.execute("SELECT cancel_requested FROM lean_jobs WHERE id=?", (job_id,)).fetchone()
                if row and row["cancel_requested"]:
                    update_job(job_id, status="cancelled", error="")
                    return
                outcome = runner(toolchain, source_path, workdir, timeout_seconds, register_process(job_id))
            if outcome["status"] == "failed" and outcome["exit_code"] == -signal.SIGKILL:
                with connect(db_path) as db:
                    row = db.execute("SELECT cancel_requested FROM lean_jobs WHERE id=?", (job_id,)).fetchone()
                if row and row["cancel_requested"]:
                    # Our own cancel killed the process group; report the
                    # cancellation instead of a compiler failure.
                    outcome = {**outcome, "status": "cancelled"}
            update_job(
                job_id,
                status=outcome["status"],
                # A cancelled run has no verification result; timeouts and runner
                # errors keep their partial outcome for diagnosis.
                result="" if outcome["status"] == "cancelled" else json.dumps(outcome),
                error="" if outcome["status"] in ("verified", "failed") else f"LEAN_{outcome['status'].upper()}",
            )
            count(outcome["status"])
            log_event("verify.completed", job_id=job_id, status=outcome["status"], walltime_ms=outcome["walltime_ms"])
        finally:
            with running_lock:
                running.pop(job_id, None)
            shutil.rmtree(workdir, ignore_errors=True)
            # The submitted source stays until terminal-history retention prunes
            # the job (bounded by JOB_HISTORY), so a crash/restart can requeue it.

    def run_job(job_id):
        row = fetch_job(job_id)
        if not row or row["status"] not in ("queued", "running"):
            return
        try:
            toolchain = toolchain_path(row["toolchain"])
        except APIError as exc:
            update_job(job_id, status="failed", error=f"{exc.code}: {exc.message}")
            return
        update_job(job_id, status="running")
        execute(job_id, toolchain, row["source_path"], row["timeout"])

    def resume_jobs():
        with transaction(db_path) as db:
            rows = db.execute("SELECT id FROM lean_jobs WHERE status IN ('queued','running')").fetchall()
        for row in rows:
            update_job(row["id"], status="queued")
            pool.submit(run_job, row["id"])
        return bool(rows)

    def configured():
        return bool(load_toolchains(os.getenv("LEAN_TOOLCHAINS_FILE", ""))) and sandbox_status()["isolated"]

    app.state.ready_check = configured

    @app.post("/api/v1/lean/verify")
    def verify(body: VerifyRequest, request: Request, actor=Depends(scope("lean:verify"))):
        if body.network:
            raise APIError(
                422, "NETWORK_FORBIDDEN",
                "verification runs are always network-isolated; network access cannot be requested",
            )
        toolchain = toolchain_path(body.toolchain)
        if body.wait:
            if not admission.acquire(blocking=False):
                raise APIError(429, "LEAN_BUSY", "verification capacity is busy; retry the request")
            job_dir = jobs_root / str(uuid4())
            job_dir.mkdir(mode=0o700)
            source_path = job_dir / "source.lean"
            source_path.write_text(body.source)
            try:
                outcome = runner(toolchain, source_path, job_dir, body.timeout_seconds)
            finally:
                admission.release()
                shutil.rmtree(job_dir, ignore_errors=True)
            count(outcome["status"])
            log_event("verify.completed", status=outcome["status"], walltime_ms=outcome["walltime_ms"])
            return response(request, outcome)
        if queued_count() >= MAX_QUEUED:
            raise APIError(429, "LEAN_QUEUE_FULL", "verification queue is full; retry later")
        job_id = str(uuid4())
        job_dir = jobs_root / job_id
        job_dir.mkdir(mode=0o700)
        source_path = job_dir / "source.lean"
        source_path.write_text(body.source)
        now = time.time()
        with transaction(db_path) as db:
            db.execute(
                "INSERT INTO lean_jobs(id,owner,toolchain,timeout,source_path,status,created,updated) "
                "VALUES(?,?,?,?,?,'queued',?,?)",
                (job_id, actor.sub, body.toolchain, body.timeout_seconds, str(source_path), now, now),
            )
        prune_jobs()
        pool.submit(run_job, job_id)
        return response(request, job_view(fetch_job(job_id)))

    @app.get("/api/v1/lean/jobs/{job_id}")
    def job_status(job_id: str, request: Request, actor=Depends(scope("lean:verify"))):
        return response(request, job_view(owned_job(job_id, actor)))

    @app.post("/api/v1/lean/jobs/{job_id}/cancel")
    def cancel_job(job_id: str, request: Request, actor=Depends(scope("lean:verify"))):
        row = owned_job(job_id, actor)
        if row["status"] not in TERMINAL:
            update_job(job_id, cancel_requested=1)
            with running_lock:
                process = running.get(job_id)
            if process is not None:
                _kill_group(process)
        return response(request, job_view(fetch_job(job_id)))

    existing_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        try:
            resume_jobs()
        except sqlite3.Error:
            log_event("jobs.resume_failed", level=logging.WARNING)
        async with existing_lifespan(application):
            yield

    app.router.lifespan_context = lifespan
    return app
