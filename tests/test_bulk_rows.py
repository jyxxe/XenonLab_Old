
from fastapi.testclient import TestClient

from xeonolab.bulk_jobs import BulkJobStore
from xeonolab.services import create_app

from .conftest import auth, put_acl


def make_client(settings):
    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " xelibrary:write"
    return TestClient(create_app("xelibrary", settings))


def create_table(client, name="批量表"):
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/批量/测试",
            "name": name,
            "columns": [{"name": "body", "type": "string", "nullable": False}],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["table_id"]


def row_id_of(client, table_id, body):
    """Resolve a row by its body value; uuid keying makes positional lookup meaningless."""
    rows = client.get(f"/api/v1/tables/{table_id}/rows", headers=auth()).json()["data"]
    return next(row["_row_id"] for row in rows if row["values"]["body"] == body)


def test_bulk_insert_reports_partial_success_with_error_location(settings):
    client = make_client(settings)
    table_id = create_table(client)
    response = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkInsert",
        headers=auth(),
        json={"rows": [
            {"values": {"body": "alpha"}},
            {"values": {"body": None}},
            {"values": {"unknown": "beta"}},
            {"values": {"body": "gamma"}},
        ]},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["mode"] == "sync" and data["total"] == 4
    assert data["done"] == 4 and data["failed"] == 2
    statuses = [item["status"] for item in data["results"]]
    assert statuses == ["created", "failed", "failed", "created"]
    assert data["results"][1]["error"]["code"] == "NULL_NOT_ALLOWED"
    assert data["results"][2]["error"]["code"] == "UNKNOWN_COLUMN"
    created_ids = [item["row_id"] for item in data["results"] if item["status"] == "created"]
    for rid in created_ids:
        assert client.get(f"/api/v1/tables/{table_id}/rows/{rid}", headers=auth()).status_code == 200


def test_bulk_update_stale_revision_and_per_item_conflicts(settings):
    client = make_client(settings)
    table_id = create_table(client)
    client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": "v1"}})
    client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": "v2"}})
    first, second = row_id_of(client, table_id, "v1"), row_id_of(client, table_id, "v2")
    response = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkUpdate",
        headers=auth(),
        json={"rows": [
            {"row_id": first, "revision": 1, "values": {"body": "updated"}},
            {"row_id": second, "revision": 99, "values": {"body": "stale"}},
        ]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["results"][0]["status"] == "updated" and data["results"][0]["revision"] == 2
    assert data["results"][1]["status"] == "failed"
    assert data["results"][1]["error"]["code"] == "STALE_REVISION"
    assert client.get(f"/api/v1/tables/{table_id}/rows/{second}", headers=auth()).json()["data"]["values"]["body"] == "v2"


def test_bulk_delete_soft_deletes_and_keeps_purge_admin_only(settings):
    client = make_client(settings)
    table_id = create_table(client)
    for body in ("one", "two"):
        client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": body}})
    target = row_id_of(client, table_id, "one")
    response = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkDelete",
        headers=auth(),
        json={"rows": [{"row_id": target, "revision": 1}]},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["results"][0]["status"] == "deleted"
    listed = client.get(f"/api/v1/tables/{table_id}/rows", headers=auth()).json()["data"]
    assert all(row["_row_id"] != target for row in listed)
    assert client.get(f"/api/v1/tables/{table_id}/rows/{target}", headers=auth()).status_code == 404


def test_bulk_requires_table_write_permission(settings):
    client = make_client(settings)
    table_id = create_table(client)
    assert client.post(
        f"/api/v1/tables/{table_id}/rows:bulkInsert",
        headers=auth("bob"),
        json={"rows": [{"values": {"body": "x"}}]},
    ).status_code == 403
    assert client.post(
        f"/api/v1/tables/{table_id}/rows:bulkInsert",
        json={"rows": [{"values": {"body": "x"}}]},
    ).status_code == 401


def test_bulk_respects_row_level_acl_per_item(settings):
    client = make_client(settings)
    table_id = create_table(client)
    for body in ("open", "guarded"):
        client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": body}})
    open_row, guarded = row_id_of(client, table_id, "open"), row_id_of(client, table_id, "guarded")
    put_acl(client, "table", table_id, {"bob": ["read", "write"]})
    put_acl(client, "row", f"{table_id}:{guarded}", {"bob": ["read"]})
    response = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkUpdate",
        headers=auth("bob"),
        json={"rows": [
            {"row_id": open_row, "revision": 1, "values": {"body": "bob was here"}},
            {"row_id": guarded, "revision": 1, "values": {"body": "bob denied"}},
        ]},
    )
    assert response.status_code == 200
    results = response.json()["data"]["results"]
    assert results[0]["status"] == "updated"
    assert results[1]["status"] == "failed" and results[1]["error"]["code"] == "XL_FORBIDDEN"
    assert client.get(f"/api/v1/tables/{table_id}/rows/{guarded}", headers=auth("bob")).json()["data"]["values"]["body"] == "guarded"


def test_large_batches_become_persisted_jobs_with_status(settings, monkeypatch):
    monkeypatch.setenv("XELIBRARY_BULK_SYNC_LIMIT", "3")
    client = make_client(settings)
    table_id = create_table(client)
    rows = [{"values": {"body": f"row-{index}"}} for index in range(6)]
    response = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkInsert",
        headers=auth(),
        json={"rows": rows, "batch_id": "batch-abc-123"},
    )
    assert response.status_code == 202, response.text
    data = response.json()["data"]
    assert data["mode"] == "job" and data["status"] in ("queued", "running", "completed")
    job_id = data["job_id"]
    status = client.get(f"/api/v1/tables/{table_id}/rows:bulk/{job_id}", headers=auth()).json()["data"]
    assert status["status"] == "completed" and status["done"] == 6 and status["failed"] == 0
    assert len(status["results"]) == 6
    listed = client.get(f"/api/v1/tables/{table_id}/rows?limit=100", headers=auth()).json()["data"]
    assert len(listed) == 6
    duplicate = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkInsert",
        headers=auth(),
        json={"rows": rows, "batch_id": "batch-abc-123"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["duplicate"] is True
    assert duplicate.json()["data"]["job_id"] == job_id
    assert len(client.get(f"/api/v1/tables/{table_id}/rows?limit=100", headers=auth()).json()["data"]) == 6


def test_bulk_job_status_is_scoped_and_protected(settings):
    client = make_client(settings)
    table_id = create_table(client)
    response = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkDelete",
        headers=auth(),
        json={"rows": [{"row_id": "missing-row", "revision": 1}], "batch_id": "batch-xyz-789"},
    )
    job_id = response.json()["data"]["job_id"]
    assert client.get(f"/api/v1/tables/{table_id}/rows:bulk/{job_id}", headers=auth("bob")).status_code == 403
    assert client.get(f"/api/v1/tables/{table_id}/rows:bulk/{job_id}", headers=auth("admin")).status_code == 200
    assert client.get(f"/api/v1/tables/{table_id}/rows:bulk/missing", headers=auth()).status_code == 404
    assert client.get(f"/api/v1/tables/{table_id}/rows:bulk/{job_id}", headers=auth()).json()["data"]["failed"] == 1


def test_bulk_enforces_size_cap(settings, monkeypatch):
    monkeypatch.setenv("XELIBRARY_BULK_SYNC_LIMIT", "2")
    monkeypatch.setenv("XELIBRARY_BULK_MAX_ROWS", "3")
    client = make_client(settings)
    table_id = create_table(client)
    rows = [{"values": {"body": f"row-{index}"}} for index in range(4)]
    response = client.post(
        f"/api/v1/tables/{table_id}/rows:bulkInsert", headers=auth(), json={"rows": rows}
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "BULK_TOO_LARGE"


def test_bulk_store_marks_interrupted_jobs_failed_on_restart(tmp_path):
    database = tmp_path / "catalogue.sqlite"
    store = BulkJobStore(database)
    payload = [{"values": {"body": "x"}}, {"values": {"body": "y"}}]
    job_id = store.create("table-1", "insert", payload, "alice", "batch-restart-1")
    store.start(job_id)
    store.record(job_id, {"index": 0, "status": "created", "row_id": "row-1"}, failed=False)
    reopened = BulkJobStore(database)
    job = reopened.get(job_id)
    assert job["status"] == "failed"
    assert "interrupted" in job["error"]
    assert job["done"] == 1 and job["results"][0]["status"] == "created"
    assert store.find_batch("batch-restart-1")["id"] == job_id


def test_batch_id_is_scoped_to_caller_table_and_payload(settings):
    client = make_client(settings)
    first = create_table(client, "批次一")
    second = create_table(client, "批次二")
    rows = [{"values": {"body": "same-batch"}}]
    accepted = client.post(
        f"/api/v1/tables/{first}/rows:bulkInsert",
        headers=auth(), json={"rows": rows, "batch_id": "batch-scope-0001"},
    )
    assert accepted.status_code == 200, accepted.text
    job_id = accepted.json()["data"]["job_id"]

    # The same batch_id on another table is a different scope, not a duplicate.
    other = client.post(
        f"/api/v1/tables/{second}/rows:bulkInsert",
        headers=auth(), json={"rows": rows, "batch_id": "batch-scope-0001"},
    )
    assert other.status_code == 200, other.text
    assert other.json()["data"]["job_id"] != job_id
    assert other.json()["data"].get("duplicate") is not True

    replay = client.post(
        f"/api/v1/tables/{first}/rows:bulkInsert",
        headers=auth(), json={"rows": rows, "batch_id": "batch-scope-0001"},
    )
    assert replay.status_code == 200
    assert replay.json()["data"]["duplicate"] is True
    assert replay.json()["data"]["job_id"] == job_id

    conflict = client.post(
        f"/api/v1/tables/{first}/rows:bulkInsert",
        headers=auth(),
        json={"rows": [{"values": {"body": "different payload"}}], "batch_id": "batch-scope-0001"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "BATCH_CONFLICT"

    # Another caller reusing the batch_id on the same table is a separate scope.
    assert put_acl(client, "table", first, {"bob": ["read", "write"]}).status_code == 200
    bob = client.post(
        f"/api/v1/tables/{first}/rows:bulkInsert",
        headers=auth("bob"), json={"rows": rows, "batch_id": "batch-scope-0001"},
    )
    assert bob.status_code == 200, bob.text
    assert bob.json()["data"]["job_id"] != job_id


def test_table_delete_waits_for_active_bulk_jobs(settings):
    from xeonolab.common import connect

    client = make_client(settings)
    table_id = create_table(client, "删除保护")
    store = BulkJobStore(settings.data_dir / "catalogue.sqlite")
    job_id = store.create(table_id, "insert", [{"values": {"body": "queued"}}], "alice", None)
    table = client.get(f"/api/v1/tables/{table_id}", headers=auth("admin"))
    delete = client.delete(
        f"/api/v1/tables/{table_id}",
        headers=auth("admin", **{"If-Match": table.headers["ETag"]}),
    )
    assert delete.status_code == 409, delete.text
    assert delete.json()["error"]["code"] == "TABLE_BULK_ACTIVE"
    with connect(settings.data_dir / "catalogue.sqlite") as db:
        db.execute("UPDATE bulk_jobs SET status='completed' WHERE id=?", (job_id,))


def test_recovery_reconciles_results_committed_but_not_recorded(settings, tmp_path):
    import json
    import sqlite3

    client = make_client(settings)
    table_id = create_table(client, "恢复")
    catalogue = settings.data_dir / "catalogue.sqlite"
    table_file = settings.data_dir / "tables" / f"{table_id}.xl"
    store = BulkJobStore(catalogue)
    job_id = store.create(
        table_id, "insert", [{"values": {"body": "one"}}, {"values": {"body": "two"}}], "alice", None,
    )
    store.start(job_id)
    # Simulate a crash after the first row's table commit (marker written) and
    # before the durable job record: only the marker survives.
    first_result = {"index": 0, "status": "created", "row_id": "committed-row", "revision": 1}
    with sqlite3.connect(table_file) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS __xl_bulk_applied("
            "job_id TEXT,item_index INTEGER,result TEXT NOT NULL,failed INTEGER NOT NULL,"
            "PRIMARY KEY(job_id,item_index))"
        )
        db.execute(
            "INSERT INTO __xl_bulk_applied VALUES(?,?,?,?)",
            (job_id, 0, json.dumps(first_result), 0),
        )

    reopened = BulkJobStore(catalogue)
    assert [job["id"] for job in reopened.stranded] == [job_id]

    def reader(tid, expected_job):
        with sqlite3.connect(settings.data_dir / "tables" / f"{tid}.xl") as db:
            return [
                (row[0], row[1], row[2])
                for row in db.execute(
                    "SELECT item_index,result,failed FROM __xl_bulk_applied WHERE job_id=?", (expected_job,)
                )
            ]

    reopened.recover(reader)
    job = reopened.get(job_id)
    assert job["status"] == "failed"
    assert "recovered" in job["error"]
    assert job["done"] == 1 and job["failed"] == 0
    assert job["results"] == [first_result]
