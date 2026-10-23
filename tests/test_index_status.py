from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth, put_acl
from .test_search import FakeEmbeddingClient, tiny_tokenizer


def make_client(settings, tmp_path, monkeypatch, fake_embedding=True):
    path, _ = tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "test-token")
    if fake_embedding:
        monkeypatch.setattr("xeonolab.search.httpx.Client", FakeEmbeddingClient)
    return TestClient(create_app("xelibrary", settings))


def create_table(client, name="状态表"):
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/状态/测试",
            "name": name,
            "columns": [{"name": "body", "type": "string", "nullable": False}],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["table_id"]


def test_index_status_requires_read_permission(settings, tmp_path, monkeypatch):
    client = make_client(settings, tmp_path, monkeypatch)
    table_id = create_table(client)
    assert client.get(f"/api/v1/tables/{table_id}/index-status").status_code == 401
    assert client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth("bob")).status_code == 403
    body = client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth()).json()["data"]
    assert body["state"] == "empty"
    assert body["row_count"] == 0 and body["embedded_rows"] == 0


def test_index_status_tracks_not_indexed_ready_and_stale(settings, tmp_path, monkeypatch):
    client = make_client(settings, tmp_path, monkeypatch)
    table_id = create_table(client)
    client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": "alpha beta"}})
    before = client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth()).json()["data"]
    assert before["state"] == "not_indexed"
    assert before["pending_rows"] == 1 and before["missing_rows"] == 1
    assert before["last_job"] is None

    reindex = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": table_id})
    assert reindex.status_code == 202
    job = client.get(f"/api/v1/jobs/{reindex.json()['data']['job_id']}", headers=auth("admin"))
    assert job.json()["data"]["status"] == "completed"
    ready = client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth()).json()["data"]
    assert ready["state"] == "ready"
    assert ready["row_count"] == 1 and ready["embedded_rows"] == 1 and ready["pending_rows"] == 0
    assert ready["chunk_count"] >= 1
    assert ready["last_job"]["status"] == "completed"

    client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": "gamma delta"}})
    stale = client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth()).json()["data"]
    assert stale["state"] == "stale"
    assert stale["row_count"] == 2 and stale["embedded_rows"] == 1
    assert stale["pending_rows"] == 1 and stale["missing_rows"] == 1


def test_index_status_reports_failed_job(settings, tmp_path, monkeypatch):
    client = make_client(settings, tmp_path, monkeypatch, fake_embedding=False)
    table_id = create_table(client)
    client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": "alpha beta"}})
    reindex = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": table_id})
    assert reindex.status_code == 202
    job = client.get(f"/api/v1/jobs/{reindex.json()['data']['job_id']}", headers=auth("admin"))
    assert job.json()["data"]["status"] == "failed"
    # The job owner (admin) sees the full job object including the error payload.
    failed = client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth("admin")).json()["data"]
    assert failed["state"] == "failed"
    assert failed["last_job"]["status"] == "failed"
    assert failed["last_job"]["error"]


def test_index_status_last_job_is_owner_scoped(settings, tmp_path, monkeypatch):
    """PR review: shared-table readers must not receive another caller's job
    object (owner subject, job id, error metadata) through index-status."""
    client = make_client(settings, tmp_path, monkeypatch)
    table_id = create_table(client)
    grant = put_acl(client, "table", table_id, {"bob": ["read"]})
    assert grant.status_code == 200, grant.text
    client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": "alpha beta"}})
    reindex = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": table_id})
    assert reindex.status_code == 202
    job_id = reindex.json()["data"]["job_id"]

    owner_view = client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth("admin")).json()["data"]
    assert owner_view["last_job"]["job_id"] == job_id
    assert owner_view["last_job"]["owner"] == "admin"

    reader_view = client.get(f"/api/v1/tables/{table_id}/index-status", headers=auth("bob")).json()["data"]
    # Same table-level state derivation, but the job summary is minimal.
    assert reader_view["state"] == owner_view["state"]
    assert reader_view["last_job"]["status"] in {"queued", "running", "completed", "failed"}
    assert "job_id" not in reader_view["last_job"]
    assert "owner" not in reader_view["last_job"]
    assert "error" not in reader_view["last_job"]
    # When no job exists at all the summary stays absent for everyone.
    fresh_table = create_table(client, name="无任务表")
    put_acl(client, "table", fresh_table, {"bob": ["read"]})
    assert client.get(f"/api/v1/tables/{fresh_table}/index-status", headers=auth("bob")).json()["data"]["last_job"] is None
