import json
import sqlite3
import textwrap
import time

import pytest
from fastapi.testclient import TestClient

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
    if mode == "error":
        print("source error: declaration 'target' has type mismatch", file=sys.stderr)
        sys.exit(1)
    # "ok" (and explicit "axioms") behave like a clean elaboration with a
    # #print axioms line, so tests can exercise axiom extraction.
    print("'target' depends on axioms: [propext, Lean.testAxiom]")
    sys.exit(0)
    """
)


def write_toolchain(tmp_path, body=FAKE_LEAN):
    bin_dir = tmp_path / "toolchain-bin"
    bin_dir.mkdir(exist_ok=True)
    lean = bin_dir / "lean"
    lean.write_text(body.lstrip())
    lean.chmod(0o755)
    manifest = tmp_path / "toolchains.json"
    manifest.write_text(json.dumps({"toolchains": [{"name": "lean4", "path": str(lean)}]}))
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
        current = api.get(f"/api/v1/lean/jobs/{job_id}", headers=auth()).json()["data"]
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
            {"name": "lean4", "path": str(tmp_path / "toolchain-bin" / "lean")},
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
