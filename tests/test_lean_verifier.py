import hashlib
import json
import os
import sqlite3
import textwrap
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import xeonolab.lean_verifier as lean_verifier
from xeonolab.lean_verifier import SERVICE, create_app, extract_axioms, load_toolchains
from xeonolab.services import create_app as create_service_app

from .conftest import auth

VERIFIED_SOURCE = """
theorem one_eq_one : 1 = 1 := rfl
#print axioms one_eq_one
"""

# A stand-in "lean" binary: deterministic, fast, and lets tests observe the
# sandbox contract (environment, rlimits, walltime) without a real toolchain.
FAKE_LEAN = textwrap.dedent(
    """
    #!/usr/bin/env python3
    import json, os, resource, sys, time

    source = open(sys.argv[1]).read()

    if "ENV_PROBE" in source:
        print("ENV_PROBE " + json.dumps({
            "env_keys": sorted(os.environ.keys()),
            "has_secret": "UNIT_SECRET" in os.environ,
            "has_proxy": any(k.upper().startswith(("HTTP_PROXY", "HTTPS_PROXY")) for k in os.environ),
            "cwd": os.getcwd(),
            "limits": {
                "cpu": resource.getrlimit(resource.RLIMIT_CPU)[0],
                "as_bytes": resource.getrlimit(resource.RLIMIT_AS)[0],
                "fsize": resource.getrlimit(resource.RLIMIT_FSIZE)[0],
            },
        }))
        sys.exit(0)

    mode = "ok"
    extra = ""
    if " lean-marker:" in source:
        rest = source.split(" lean-marker:", 1)[1].split()
        mode = rest[0] if rest else "ok"
        extra = rest[1] if len(rest) > 1 else ""

    if mode == "hang":
        time.sleep(30)
        sys.exit(0)
    if mode == "flood":
        sys.stdout.write("x" * 200000)
        sys.stdout.flush()
        sys.exit(0)
    if mode == "error":
        print("source error: declaration 'target' has type mismatch", file=sys.stderr)
        sys.exit(1)
    # "ok" (and explicit "axioms") behave like a clean elaboration with a
    # #print axioms line, so tests can exercise axiom extraction.
    print("'target' depends on axioms: [propext, Lean.testAxiom]")
    sys.exit(0)
    """
)


def sandbox_stat(path, *, stat_file=os.stat):
    """Model only the four namespace observations; inspect actual fixture files."""
    text = str(path)
    if text in {f"/proc/{pid}/ns/{kind}" for pid in ("self", "1") for kind in ("net", "mnt")}:
        return SimpleNamespace(
            st_uid=0, st_mode=0o100444,
            st_ino=1 if text.startswith("/proc/self/") else 2, st_size=0,
        )
    info = stat_file(path)
    return SimpleNamespace(st_uid=0, st_mode=info.st_mode, st_ino=info.st_ino, st_size=info.st_size)


@pytest.fixture(autouse=True)
def trusted_test_sandbox(monkeypatch, settings):
    """Simulate the reviewed deployment: root-owned files and active namespaces."""
    # Persisted rate limits would throttle polling across restarted app instances.
    settings.rate_limit_enabled = False
    monkeypatch.setattr(lean_verifier, "_stat", sandbox_stat)


def write_toolchain(tmp_path, body=FAKE_LEAN):
    bin_dir = tmp_path / "toolchain-bin"
    bin_dir.mkdir(exist_ok=True)
    lean = bin_dir / "lean"
    lean.write_text(body.lstrip())
    lean.chmod(0o755)
    digest = hashlib.sha256(lean.read_bytes()).hexdigest()
    manifest = tmp_path / "toolchains.json"
    manifest.write_text(json.dumps({"toolchains": [{"name": "lean4", "path": str(lean), "sha256": digest}]}))
    return manifest


@pytest.fixture
def verifier(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    settings.dev_tokens["bob"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    monkeypatch.setenv("UNIT_SECRET", "do-not-leak")
    monkeypatch.setenv("HTTP_PROXY", "http://unit-proxy:1")
    return TestClient(create_app(settings))


def wait_terminal(api, job_id, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        response = api.get(f"/api/v1/lean/jobs/{job_id}", headers=auth())
        if response.status_code == 429:
            time.sleep(0.5)
            continue
        if response.status_code != 200:
            raise AssertionError(f"job read failed: {response.status_code} {response.text[:300]}")
        current = response.json()["data"]
        if current["status"] in ("verified", "failed", "timeout", "cancelled", "runner_error"):
            return current
        time.sleep(0.05)
    return current


def test_unconfigured_toolchain_fails_closed(settings, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(settings.data_dir / "missing.json"))
    api = TestClient(create_app(settings))
    result = api.post(
        "/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE, "timeout_seconds": 5}
    )
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "VERIFIER_UNAVAILABLE"
    assert api.get("/health/ready").status_code == 503


def test_unreadable_manifest_fails_closed(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    manifest = tmp_path / "broken.json"
    manifest.write_text("{not json")
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(manifest))
    api = TestClient(create_app(settings))
    assert (
        api.post("/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE}).status_code == 503
    )
    assert load_toolchains(str(manifest)) == {}


def test_load_toolchains_rejects_relative_paths_and_bad_names(tmp_path):
    write_toolchain(tmp_path)
    manifest = tmp_path / "mixed.json"
    manifest.write_text(json.dumps({
        "toolchains": [
            {"name": "relative", "path": "bin/lean"},
            {"name": "../escape", "path": "/bin/sh"},
            {"name": "", "path": "/bin/sh"},
            {"name": "missing", "path": "/nonexistent/lean"},
            {"name": "lean4", "path": str(tmp_path / "toolchain-bin" / "lean"),
             "sha256": hashlib.sha256((tmp_path / "toolchain-bin" / "lean").read_bytes()).hexdigest()},
        ]
    }))
    assert list(load_toolchains(str(manifest))) == ["lean4"]


def test_synchronous_verification_reports_success_and_axioms(verifier):
    result = verifier.post(
        "/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE, "timeout_seconds": 10}
    )
    assert result.status_code == 200
    outcome = result.json()["data"]
    assert outcome["status"] == "verified"
    assert outcome["exit_code"] == 0
    assert outcome["axioms"] == ["Lean.testAxiom", "propext"]
    assert outcome["walltime_ms"] >= 0
    assert "request_id" in result.json()["meta"]


def test_verification_failure_carries_compiler_output(verifier):
    result = verifier.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE + " lean-marker: error", "timeout_seconds": 10},
    )
    outcome = result.json()["data"]
    assert outcome["status"] == "failed"
    assert outcome["exit_code"] == 1
    assert "type mismatch" in outcome["stderr"]
    assert "request_id" in result.json()["meta"]


def test_child_contract_strips_environment_and_applies_rlimits(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    monkeypatch.setenv("UNIT_SECRET", "do-not-leak")
    monkeypatch.setenv("HTTP_PROXY", "http://unit-proxy:1")
    api = TestClient(create_app(settings))
    result = api.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": "-- ENV_PROBE\n" + VERIFIED_SOURCE, "timeout_seconds": 10},
    )
    outcome = result.json()["data"]
    assert outcome["status"] == "verified", outcome
    probe = json.loads(outcome["stdout"].split("ENV_PROBE ", 1)[1])
    assert probe["has_secret"] is False, "credentials must never reach the child environment"
    assert probe["has_proxy"] is False, "proxy configuration must never reach the child"
    assert probe["env_keys"] == ["HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"], "environment is the fixed minimal set"
    assert probe["limits"]["cpu"] == 10, "CPU rlimit follows the request walltime"
    assert probe["limits"]["as_bytes"] == 2 * 1024 * 1024 * 1024
    assert probe["cwd"].startswith(str(settings.data_dir / "lean-jobs")), "child runs in the service job directory"


def test_walltime_timeout_kills_run(verifier):
    started = time.monotonic()
    result = verifier.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE + " lean-marker: hang", "timeout_seconds": 2},
    )
    outcome = result.json()["data"]
    assert outcome["status"] == "timeout"
    assert time.monotonic() - started < 15, "walltime is enforced well below the fake 30s sleep"


def test_network_access_is_rejected_by_contract(verifier):
    result = verifier.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE, "network": True, "timeout_seconds": 5},
    )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "NETWORK_FORBIDDEN"


def test_unknown_toolchain_name_is_rejected(verifier):
    result = verifier.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE, "toolchain": "lean9", "timeout_seconds": 5},
    )
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "VERIFIER_UNAVAILABLE"


def test_source_size_and_timeout_are_bounded(verifier):
    assert (
        verifier.post(
            "/api/v1/lean/verify", headers=auth(), json={"source": "x" * (256 * 1024 + 1), "timeout_seconds": 5}
        ).status_code
        == 422
    )
    assert (
        verifier.post(
            "/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE, "timeout_seconds": 121}
        ).status_code
        == 422
    )


def test_durable_job_lifecycle(verifier):
    created = verifier.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE, "timeout_seconds": 10, "wait": False},
    )
    job = created.json()["data"]
    assert job["status"] in ("queued", "running")
    job_id = job["id"]

    current = wait_terminal(verifier, job_id)
    assert current["status"] == "verified", current
    assert current["result"]["axioms"] == ["Lean.testAxiom", "propext"]
    assert current["error"] is None

    cancelled = verifier.post(f"/api/v1/lean/jobs/{job_id}/cancel", headers=auth()).json()["data"]
    assert cancelled["status"] == "verified", "terminal jobs keep their outcome through cancel"
    assert verifier.get("/api/v1/lean/jobs/does-not-exist", headers=auth()).status_code == 404


def test_cancel_running_job_kills_process(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    api = TestClient(create_app(settings))
    created = api.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE + " lean-marker: hang", "timeout_seconds": 30, "wait": False},
    )
    job_id = created.json()["data"]["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if api.get(f"/api/v1/lean/jobs/{job_id}", headers=auth()).json()["data"]["status"] == "running":
            break
        time.sleep(0.05)

    started = time.monotonic()
    cancelled = api.post(f"/api/v1/lean/jobs/{job_id}/cancel", headers=auth()).json()["data"]
    assert cancelled["cancel_requested"] is True
    current = wait_terminal(api, job_id)
    assert current["status"] == "cancelled", current
    assert time.monotonic() - started < 5, "cancel kills the process group instead of waiting out the walltime"


def test_cancel_queued_job_never_runs(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    api = TestClient(create_app(settings))
    created = api.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE, "timeout_seconds": 10, "wait": False},
    )
    job_id = created.json()["data"]["id"]
    cancelled = api.post(f"/api/v1/lean/jobs/{job_id}/cancel", headers=auth()).json()["data"]
    assert cancelled["cancel_requested"] is True
    current = wait_terminal(api, job_id)
    assert current["status"] in ("cancelled", "verified"), "a won race still produces a legal terminal state"
    if current["status"] == "cancelled":
        assert current["result"] is None


def test_jobs_survive_restart_and_requeue(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    with TestClient(create_app(settings)) as api:
        created = api.post(
            "/api/v1/lean/verify",
            headers=auth(),
            json={"source": VERIFIED_SOURCE, "timeout_seconds": 10, "wait": False},
        )
        job_id = created.json()["data"]["id"]
        assert wait_terminal(api, job_id)["status"] == "verified"

    # A crash between queue and execution leaves the job non-terminal; restart requeues it.
    with sqlite3.connect(settings.data_dir / "lean-verifier.sqlite") as db:
        db.execute("UPDATE lean_jobs SET status='running' WHERE id=?", (job_id,))
    with TestClient(create_app(settings)) as api:
        assert wait_terminal(api, job_id)["status"] == "verified"


def test_scope_enforced_for_tokens_without_lean_scope(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] = "desktop:read"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    api = TestClient(create_app(settings))
    result = api.post(
        "/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE, "timeout_seconds": 5}
    )
    assert result.status_code == 403
    assert "lean:verify" in result.json()["error"]["message"]


def test_metrics_count_outcomes(verifier):
    verifier.post("/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE, "timeout_seconds": 10})
    verifier.post(
        "/api/v1/lean/verify",
        headers=auth(),
        json={"source": VERIFIED_SOURCE + " lean-marker: error", "timeout_seconds": 10},
    )
    metrics = verifier.get("/metrics").text
    import re as _re

    outcomes = {}
    for line in metrics.splitlines():
        if not line.startswith("leanverifier_verifications_total{"):
            continue
        status = _re.search(r'status="([a-z_]+)"', line).group(1)
        outcomes[status] = float(line.rsplit(" ", 1)[1])
    assert outcomes.get("verified") == 1.0 and outcomes.get("failed") == 1.0


def test_dispatcher_creates_service(settings, monkeypatch, tmp_path):
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    api = TestClient(create_service_app(SERVICE, settings))
    assert (
        api.post("/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE, "timeout_seconds": 10}).status_code
        == 200
    )


def test_extract_axioms_handles_declared_and_printed():
    source = "axiom myAxiom : False\naxiom helper : True\n"
    stdout = "'target' depends on axioms: [myAxiom, \"Quot.sound\"]\n"
    assert extract_axioms(source, stdout) == ["Quot.sound", "helper", "myAxiom"]
    assert extract_axioms("", "") == []


def test_job_ownership_is_enforced_on_status_and_cancel(verifier):
    created = verifier.post(
        "/api/v1/lean/verify", headers=auth(),
        json={"source": VERIFIED_SOURCE, "timeout_seconds": 10, "wait": False},
    )
    job_id = created.json()["data"]["id"]
    assert verifier.get(f"/api/v1/lean/jobs/{job_id}", headers=auth("bob")).status_code == 403
    cancelled = verifier.post(f"/api/v1/lean/jobs/{job_id}/cancel", headers=auth("bob"))
    assert cancelled.status_code == 403
    assert cancelled.json()["error"]["code"] == "JOB_FORBIDDEN"
    assert wait_terminal(verifier, job_id)["status"] == "verified"


def test_untrusted_manifest_and_sha_mismatch_fail_closed(settings, monkeypatch, tmp_path):
    import xeonolab.lean_verifier as lv

    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    manifest = tmp_path / "toolchains.json"
    real_stat = os.stat

    def non_root(path):
        info = real_stat(str(path))
        uid = 1000 if str(path).endswith(("toolchains.json", "lean")) else 0
        return SimpleNamespace(st_uid=uid, st_mode=info.st_mode, st_ino=info.st_ino, st_size=info.st_size)

    monkeypatch.setattr(lv, "_stat", non_root)
    assert load_toolchains(str(manifest)) == {}

    monkeypatch.undo()
    # Restore the same trusted file/namespace seam used by the fixture.
    monkeypatch.setattr(lv, "_stat", sandbox_stat)
    tampered = json.loads(manifest.read_text())
    tampered["toolchains"][0]["sha256"] = "0" * 64
    manifest.write_text(json.dumps(tampered))
    assert load_toolchains(str(manifest)) == {}


def test_service_unavailable_without_observable_sandbox(settings, monkeypatch, tmp_path):
    import xeonolab.lean_verifier as lv

    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    monkeypatch.setattr(lv, "_namespace_isolated", lambda: False)
    api = TestClient(create_app(settings))
    assert api.get("/health/ready").status_code == 503
    result = api.post("/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "VERIFIER_UNAVAILABLE"


def test_shared_admission_and_queue_bounds(settings, monkeypatch, tmp_path):
    import xeonolab.lean_verifier as lv

    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(tmp_path / "toolchains.json"))
    monkeypatch.setattr(lv, "MAX_CONCURRENT", 1)
    monkeypatch.setattr(lv, "MAX_QUEUED", 1)
    api = TestClient(create_app(settings))
    running = api.post(
        "/api/v1/lean/verify", headers=auth(),
        json={"source": VERIFIED_SOURCE + " lean-marker: hang", "timeout_seconds": 30, "wait": False},
    )
    job_id = running.json()["data"]["id"]
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if api.get(f"/api/v1/lean/jobs/{job_id}", headers=auth()).json()["data"]["status"] == "running":
            break
        time.sleep(0.02)
    # The async worker may still be between "running" and acquiring admission;
    # retry until the hang job actually holds the only slot.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        busy = api.post("/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE, "timeout_seconds": 5})
        if busy.status_code == 429:
            break
        time.sleep(0.02)
    assert busy.status_code == 429, busy.text
    assert busy.json()["error"]["code"] == "LEAN_BUSY"
    queued = api.post(
        "/api/v1/lean/verify", headers=auth(),
        json={"source": VERIFIED_SOURCE, "timeout_seconds": 5, "wait": False},
    )
    assert queued.status_code == 429, queued.text
    assert queued.json()["error"]["code"] == "LEAN_QUEUE_FULL"
    api.post(f"/api/v1/lean/jobs/{job_id}/cancel", headers=auth())


def test_output_is_bounded_while_the_process_runs(verifier):
    result = verifier.post(
        "/api/v1/lean/verify", headers=auth(),
        json={"source": VERIFIED_SOURCE + " lean-marker: flood", "timeout_seconds": 10},
    )
    outcome = result.json()["data"]
    assert outcome["status"] == "verified"
    assert outcome["stdout_truncated"] is True
    assert len(outcome["stdout"]) <= 16 * 1024
    assert outcome["dependency_lock_checked"] is False


def test_simulated_sandbox_never_reads_host_namespaces(settings, monkeypatch, tmp_path):
    """A restricted /proc must not prevent tests of the simulated sandbox."""
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    manifest = write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(manifest))
    real_stat = os.stat
    observed = []

    def deny_namespace_reads(path):
        observed.append(str(path))
        if str(path).startswith("/proc/"):
            raise PermissionError("synthetic proc restriction")
        return real_stat(path)

    monkeypatch.setattr(
        lean_verifier, "_stat", lambda path: sandbox_stat(path, stat_file=deny_namespace_reads)
    )
    api = TestClient(create_app(settings))
    assert api.get("/health/ready").status_code == 200
    result = api.post("/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE})
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "verified"
    assert str(manifest) in observed
    assert not any(path.startswith("/proc/") for path in observed)
    with pytest.raises(PermissionError):
        deny_namespace_reads("/proc/1/ns/net")


@pytest.mark.parametrize("kind", ["net", "mnt"])
@pytest.mark.parametrize("failure", ["shared", "permission", "missing"])
def test_namespace_observation_failure_denies_readiness_and_execution(
    settings, monkeypatch, tmp_path, kind, failure,
):
    """Exercise the real detector, including its OSError handling; never skip it."""
    settings.dev_tokens["alice"]["scope"] += " lean:verify"
    manifest = write_toolchain(tmp_path)
    monkeypatch.setenv("LEAN_TOOLCHAINS_FILE", str(manifest))
    observed = []

    def namespace_stat(path):
        observed.append(str(path))
        if str(path) == f"/proc/1/ns/{kind}":
            if failure == "permission":
                raise PermissionError("synthetic proc restriction")
            if failure == "missing":
                raise FileNotFoundError("synthetic missing namespace")
            return SimpleNamespace(st_ino=1)  # same as this process, not isolated
        return sandbox_stat(path)

    monkeypatch.setattr(lean_verifier, "_stat", namespace_stat)
    assert lean_verifier._namespace_isolated() is False
    api = TestClient(create_app(settings))
    assert api.get("/health/ready").status_code == 503
    result = api.post("/api/v1/lean/verify", headers=auth(), json={"source": VERIFIED_SOURCE})
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "VERIFIER_UNAVAILABLE"
    assert f"/proc/1/ns/{kind}" in observed
