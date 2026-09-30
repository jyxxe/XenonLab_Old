import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

from xeonolab.common import Metrics, base_app
from xeonolab.services import create_app

from .conftest import auth


def test_registry_render_format_escaping_and_histograms():
    metrics = Metrics("demo")
    metrics.counter("demo_requests_total", help="Requests handled.")
    metrics.counter("demo_requests_total", 2, route="/a", method="GET")
    metrics.gauge("demo_gauge", 3.5, label='quote"back\\slash')
    metrics.observe("demo_latency_seconds", 0.012, label="x")
    metrics.observe("demo_latency_seconds", 2.0, label="x")
    metrics.gauge_provider("demo_dynamic", "Dynamic gauge.", lambda: [({"kind": "a"}, 7), (None, 0)])
    text = metrics.render()
    assert "# TYPE demo_requests_total counter" in text
    assert "# HELP demo_requests_total Requests handled." in text
    assert 'demo_requests_total{method="GET",route="/a"} 2' in text
    assert 'demo_gauge{label="quote\\"back\\\\slash"} 3.5' in text
    assert "demo_dynamic{kind=\"a\"} 7" in text
    assert "demo_dynamic 0" in text
    assert 'demo_latency_seconds_bucket{label="x",le="0.025"} 1' in text
    assert 'demo_latency_seconds_bucket{label="x",le="2.5"} 2' in text
    assert 'demo_latency_seconds_bucket{label="x",le="+Inf"} 2' in text
    assert 'demo_latency_seconds_sum{label="x"} 2.012' in text
    assert 'demo_latency_seconds_count{label="x"} 2' in text
    assert text.count("# TYPE demo_requests_total") == 1
    assert text.endswith("\n")


def test_registry_rejects_type_conflicts_and_survives_broken_providers():
    metrics = Metrics()
    metrics.counter("demo_total")
    try:
        metrics.gauge("demo_total", 1)
    except ValueError as error:
        assert "already registered" in str(error)
    else:
        raise AssertionError("counter/gauge type conflict must be rejected")

    def broken():
        raise RuntimeError("database offline")

    metrics.gauge_provider("demo_broken", "Broken provider.", broken)
    text = metrics.render()
    assert "# TYPE demo_broken gauge" in text
    assert not any(line.startswith(("demo_broken{", "demo_broken ")) for line in text.splitlines())


def test_base_app_metrics_track_requests_errors_and_routes():
    client = TestClient(base_app("probe"))
    assert client.get("/health/live").status_code == 200
    assert client.get("/missing").status_code == 404
    response = client.get("/metrics")
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert 'xeonolab_requests_total{service="probe"} 2' in text
    assert 'xeonolab_errors_total{service="probe"} 1' in text
    assert 'xeonolab_build_info{service="probe",version="0.1.0"} 1' in text
    assert 'xeonolab_route_requests_total{method="GET",route="/health/live",service="probe",status="200"} 1' in text
    assert 'route="unmatched",service="probe",status="404"' in text
    assert "xeonolab_request_duration_seconds_count" in text


def seed_table(client):
    result = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/metrics",
            "name": "演示",
            "columns": [{"name": "title", "type": "string", "nullable": False}],
        },
    )
    assert result.status_code == 201, result.text
    tid = result.json()["data"]["table_id"]
    created = client.post(
        f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"title": "示例"}}
    )
    assert created.status_code == 201
    return tid


def test_xelibrary_query_metrics_and_state_gauges(library):
    seed_table(library)
    assert library.post("/api/v1/query", headers=auth(), json={"expression": 'FROM "/metrics/**"'}).status_code == 200
    assert (
        library.post("/api/v1/query", headers=auth(), json={"expression": "NOT A QUERY"}).status_code == 422
    )
    text = library.get("/metrics").text
    assert "xelibrary_query_total 1" in text
    assert "xelibrary_query_errors_total 1" in text
    assert "xelibrary_query_duration_seconds_count 2" in text
    assert "xelibrary_rows_total 1" in text
    assert "xelibrary_chunks_total 0" in text
    assert "xelibrary_index_lag 1" in text
    assert "xelibrary_embedding_backlog 0" in text
    assert "xelibrary_snapshot_success 1" in text
    storage = [line for line in text.splitlines() if line.startswith("xelibrary_storage_bytes ")]
    assert len(storage) == 1 and int(storage[0].split()[1]) > 0


def test_library_snapshot_refresh_is_cached_and_single_flight(library, monkeypatch):
    seed_table(library)
    import xeonolab.library as library_module

    original_connect = library_module.connect
    opened = []

    def counted_connect(path):
        opened.append(Path(path).name)
        return original_connect(path)

    monkeypatch.setattr(library_module, "connect", counted_connect)
    barrier = threading.Barrier(4)

    def scrape(_):
        barrier.wait(timeout=5)
        return library.app.state.metrics.render()

    with ThreadPoolExecutor(4) as pool:
        samples = list(pool.map(scrape, range(4)))

    assert all("xelibrary_snapshot_success 1" in sample for sample in samples)
    # One refresh reads the catalogue, validates the table mapping, and opens
    # the .xl database once; the concurrent scrapes share its cached result.
    assert opened.count("catalogue.sqlite") == 2
    assert len(opened) == 3
    library.app.state.metrics.render()
    assert len(opened) == 3


def test_library_snapshot_database_failure_omits_state_instead_of_reporting_zero(library, settings):
    seed_table(library)
    catalogue = settings.data_dir / "catalogue.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.execute("DROP TABLE tables")

    text = library.get("/metrics").text
    assert "xelibrary_snapshot_success 0" in text
    for metric in (
        "xelibrary_rows_total",
        "xelibrary_chunks_total",
        "xelibrary_index_lag",
        "xelibrary_embedding_backlog",
        "xelibrary_storage_bytes",
    ):
        assert not any(line.startswith(metric + " ") for line in text.splitlines())


def test_library_snapshot_table_database_failure_omits_state_instead_of_reporting_zero(library, settings):
    tid = seed_table(library)
    table_file = settings.data_dir / "tables" / f"{tid}.xl"
    table_file.unlink()

    text = library.get("/metrics").text
    assert "xelibrary_snapshot_success 0" in text
    assert not any(line.startswith("xelibrary_rows_total ") for line in text.splitlines())
    assert not any(line.startswith("xelibrary_index_lag ") for line in text.splitlines())


def test_embedding_metrics_by_model(settings):
    settings.dev_tokens["alice"]["scope"] += " embedding:write"
    with TestClient(create_app("embedding", settings)) as client:
        assert client.post("/v1/embeddings", headers=auth(), json={"input": "test"}).status_code == 503
        assert (
            client.post("/v1/embeddings", headers=auth(), json={"model": "quality", "input": "test"}).status_code
            == 503
        )
        text = client.get("/metrics").text
    assert 'embedding_model_loaded{model="d100m"} 0' in text
    assert 'embedding_model_loaded{model="qwen3vl2b"} 0' in text
    assert 'embedding_requests_total{model="d100m",result="error"} 1' in text
    assert 'embedding_requests_total{model="qwen3vl2b",result="error"} 1' in text
    assert 'embedding_errors_total{code="MODEL_NOT_CONFIGURED",model="d100m"} 1' in text
    assert 'embedding_errors_total{code="MODEL_NOT_CONFIGURED",model="qwen3vl2b"} 1' in text
    assert "embedding_latency_seconds_count" in text
    assert 'embedding_queue_depth{model="d100m"} 0' in text
    assert 'embedding_in_flight{model="d100m"} 0' in text


def test_embedding_worker_busy_is_reported_as_in_flight_not_queued(settings):
    from types import SimpleNamespace

    settings.dev_tokens["alice"]["scope"] += " embedding:write"
    with TestClient(create_app("embedding", settings)) as client:
        lock = threading.Lock()
        client.app.state.model = SimpleNamespace(lock=lock, revision="test")
        idle = client.get("/metrics").text
        lock.acquire()
        try:
            busy = client.get("/metrics").text
        finally:
            lock.release()

    assert 'embedding_queue_depth{model="d100m"} 0' in idle
    assert 'embedding_in_flight{model="d100m"} 0' in idle
    assert 'embedding_queue_depth{model="d100m"} 0' in busy
    assert 'embedding_in_flight{model="d100m"} 1' in busy


def test_deploy_metrics_follow_real_state_machine(settings, tmp_path, monkeypatch):
    client = TestClient(create_app("deploy-gateway", settings))
    release = client.post(
        "/api/v1/releases",
        headers=auth(),
        json={
            "commit": "a" * 40,
            "artifacts": [{"name": "xelibrary", "digest": "sha256:" + "b" * 64}],
            "migration_manifest": "none",
            "test_report": "test-suite-passed",
        },
    )
    rid = release.json()["data"]["release_id"]
    assert client.post(f"/api/v1/releases/{rid}/approve", headers=auth("admin")).status_code == 200
    acquired = client.post(
        "/api/v1/deploy/lease/acquire",
        headers=auth("alice"),
        json={"agent_id": "alice", "release_id": rid},
    )
    lease = acquired.json()["data"]
    assert 'deploy_lease_active{service="deploy-gateway"} 1' in client.get("/metrics").text

    executor = tmp_path / "executor.sh"
    executor.write_text('#!/bin/sh\ncat >/dev/null\nprintf \'{"state":"rolled_back"}\'\n')
    executor.chmod(0o755)
    monkeypatch.setenv("DEPLOY_EXECUTOR_PATH", str(executor))
    body = {"release_id": rid, "lease_id": lease["lease_id"], "epoch": lease["epoch"]}
    assert client.post("/api/v1/deployments", headers=auth("alice"), json=body).json()["data"]["state"] == "rolled_back"
    text = client.get("/metrics").text
    assert "deploy_rollback_total 1" in text
    assert "deploy_failure_total" not in text
    assert "deploy_duration_seconds_count 1" in text
    assert 'deploy_deployments{service="deploy-gateway",state="rolled_back"} 1' in text

    executor.write_text("#!/bin/sh\ncat >/dev/null\nexit 1\n")
    assert client.post("/api/v1/deployments", headers=auth("alice"), json=body).json()["data"]["state"] == "failed"
    text = client.get("/metrics").text
    assert "deploy_failure_total 1" in text
    assert 'deploy_deployments{service="deploy-gateway",state="failed"} 1' in text
