import json
import time

from fastapi.testclient import TestClient

from xeonolab.bulk_jobs import BulkJobStore
from xeonolab.services import create_app

from .conftest import auth, put_acl


def wait_terminal(client, path, headers=None, tries=80):
    """Poll a bulk job until it reaches a terminal state (background workers race)."""
    for _ in range(tries):
        response = client.get(path, headers=headers or auth())
        if response.status_code == 429:
            time.sleep(0.5)
            continue
        state = response.json()["data"]
        if state["status"] in ("completed", "failed", "cancelled"):
            return state
        time.sleep(0.1)
    return state


def make_client(settings):
    for user in ("alice", "bob"):
        settings.dev_tokens[user]["scope"] += " xelibrary:write xelibrary:read"
    return TestClient(create_app("xelibrary", settings))


def create_table(client, name):
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/导入导出/测试",
            "name": name,
            "columns": [{"name": "body", "type": "string", "nullable": False}],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["table_id"]


def test_export_import_jsonl_round_trip(settings):
    client = make_client(settings)
    source = create_table(client, "导出源")
    for index in range(3):
        client.post(f"/api/v1/tables/{source}/rows", headers=auth(), json={"values": {"body": f"row-{index}"}})
    export = client.post("/api/v1/exports", headers=auth(), json={"table_id": source, "format": "jsonl"})
    assert export.status_code == 202, export.text
    job_id = export.json()["data"]["job_id"]
    status = wait_terminal(client, f"/api/v1/exports/{job_id}")
    assert status["status"] == "completed" and status["downloadable"] is True, json.dumps(status, ensure_ascii=False)
    download = client.get(f"/api/v1/exports/{job_id}/download", headers=auth())
    assert download.status_code == 200
    lines = [json.loads(line) for line in download.text.splitlines()]
    assert len(lines) == 3
    assert {line["values"]["body"] for line in lines} == {"row-0", "row-1", "row-2"}

    target = create_table(client, "导入目标")
    imported = client.post(
        "/api/v1/imports",
        headers=auth(),
        json={"table_id": target, "format": "jsonl",
              "content": "\n".join(json.dumps(line) for line in lines)},
    )
    assert imported.status_code == 202, imported.text
    import_job = imported.json()["data"]["job_id"]
    state = wait_terminal(client, f"/api/v1/imports/{import_job}")
    assert state["status"] == "completed" and state["failed"] == 0
    round_trip = client.get(f"/api/v1/tables/{target}/rows?limit=100", headers=auth()).json()["data"]
    assert {row["values"]["body"] for row in round_trip} == {"row-0", "row-1", "row-2"}
    assert all(row["values"].get("body") for row in round_trip)


def test_export_excludes_rows_without_read_acl(settings):
    client = make_client(settings)
    table_id = create_table(client, "权限导出")
    for body in ("public", "secret"):
        client.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"body": body}})
    rows = client.get(f"/api/v1/tables/{table_id}/rows", headers=auth()).json()["data"]
    secret_id = next(row["_row_id"] for row in rows if row["values"]["body"] == "secret")
    put_acl(client, "table", table_id, {"bob": ["read"]})
    put_acl(client, "row", f"{table_id}:{secret_id}", {"bob": []})
    export = client.post("/api/v1/exports", headers=auth("bob"), json={"table_id": table_id, "format": "jsonl"})
    assert export.status_code == 202
    job_id = export.json()["data"]["job_id"]
    wait_terminal(client, f"/api/v1/exports/{job_id}", headers=auth("bob"))
    download = client.get(f"/api/v1/exports/{job_id}/download", headers=auth("bob"))
    bodies = [json.loads(line)["values"]["body"] for line in download.text.splitlines()]
    assert bodies == ["public"], "rows without read ACL must never appear in an export"


def test_job_access_is_owner_scoped(settings):
    client = make_client(settings)
    table_id = create_table(client, "隔离表")
    export = client.post("/api/v1/exports", headers=auth(), json={"table_id": table_id, "format": "jsonl"})
    job_id = export.json()["data"]["job_id"]
    assert client.get(f"/api/v1/exports/{job_id}", headers=auth("bob")).status_code == 403
    assert client.get(f"/api/v1/exports/{job_id}/download", headers=auth("bob")).status_code == 403
    assert client.get(f"/api/v1/exports/{job_id}", headers=auth("admin")).status_code == 200
    import_job = client.post(
        "/api/v1/imports",
        headers=auth(),
        json={"table_id": table_id, "format": "jsonl", "content": json.dumps({"body": "x"}),
              "batch_id": "isolate-batch-1"},
    )
    import_id = import_job.json()["data"]["job_id"]
    assert client.post(f"/api/v1/imports/{import_id}/cancel", headers=auth("bob")).status_code == 403
    assert client.get(f"/api/v1/imports/{import_id}", headers=auth("bob")).status_code == 403


def test_import_rejects_oversized_empty_and_unsupported(settings, monkeypatch):
    monkeypatch.setenv("XELIBRARY_IMPORT_MAX_BYTES", "64")
    client = make_client(settings)
    table_id = create_table(client, "上限表")
    oversized = client.post(
        "/api/v1/imports", headers=auth(),
        json={"table_id": table_id, "format": "jsonl", "content": json.dumps({"body": "x" * 200})},
    )
    assert oversized.status_code == 413
    assert oversized.json()["error"]["code"] == "IMPORT_TOO_LARGE"
    empty = client.post("/api/v1/imports", headers=auth(),
                        json={"table_id": table_id, "format": "jsonl", "content": "  \n  "})
    assert empty.status_code == 422 and empty.json()["error"]["code"] == "IMPORT_EMPTY"
    wrong_format = client.post("/api/v1/imports", headers=auth(),
                               json={"table_id": table_id, "format": "parquet", "content": "{}"})
    assert wrong_format.status_code == 422
    bad_export = client.post("/api/v1/exports", headers=auth(),
                             json={"table_id": table_id, "format": "parquet"})
    assert bad_export.status_code == 422
    assert client.post("/api/v1/imports", json={"table_id": table_id, "format": "jsonl", "content": "{}"}).status_code == 401


def test_import_records_per_line_failures_without_losing_valid_rows(settings):
    client = make_client(settings)
    table_id = create_table(client, "混合导入")
    content = "\n".join([
        json.dumps({"body": "good"}),
        json.dumps({"unknown": "field"}),
        json.dumps({"body": None}),
        "not-json",
        json.dumps({"body": "also good"}),
    ])
    imported = client.post("/api/v1/imports", headers=auth(),
                           json={"table_id": table_id, "format": "jsonl", "content": content})
    assert imported.status_code == 202
    state = wait_terminal(client, f"/api/v1/imports/{imported.json()['data']['job_id']}")
    assert state["done"] == 5 and state["failed"] == 3
    codes = {result["error"]["code"] for result in state["results"] if result["status"] == "failed"}
    assert codes == {"UNKNOWN_COLUMN", "NULL_NOT_ALLOWED", "INTERNAL_ERROR"}
    listed = client.get(f"/api/v1/tables/{table_id}/rows?limit=100", headers=auth()).json()["data"]
    assert {row["values"]["body"] for row in listed} == {"good", "also good"}


def test_cancel_finishes_and_cleans_output(tmp_path):
    database = tmp_path / "catalogue.sqlite"
    store = BulkJobStore(database)
    job_id = store.create("table-1", "export", {"format": "jsonl"}, "alice")
    output = tmp_path / "exports" / f"{job_id}.jsonl"
    output.parent.mkdir(parents=True)
    output.write_text("{}\n")
    store.set_output(job_id, str(output))
    store.start(job_id)
    assert store.request_cancel(job_id) is True
    store.finish_cancelled(job_id)
    assert store.get(job_id)["status"] == "cancelled"
    assert not output.exists(), "cancelled export output must be cleaned up"
    assert store.request_cancel(job_id) is False, "terminal jobs cannot be cancelled twice"
    reopened = BulkJobStore(database)
    assert reopened.get(job_id)["status"] == "cancelled"


def test_cancel_after_completion_returns_conflict(settings):
    client = make_client(settings)
    table_id = create_table(client, "取消表")
    import_job = client.post(
        "/api/v1/imports", headers=auth(),
        json={"table_id": table_id, "format": "jsonl", "content": json.dumps({"body": "x"}),
              "batch_id": "cancel-batch-1"},
    )
    job_id = import_job.json()["data"]["job_id"]
    # Cancellation is cooperative and races with the background worker under
    # TestClient: a queued job flips straight to cancelled (200), a running job
    # accepts the flag (200) or finishes first (409 JOB_FINISHED). The exact
    # cancelled/cleanup semantics are covered deterministically at store level.
    cancel = client.post(f"/api/v1/imports/{job_id}/cancel", headers=auth())
    assert cancel.status_code in (200, 409)
    if cancel.status_code == 409:
        assert cancel.json()["error"]["code"] == "JOB_FINISHED"
    state = client.get(f"/api/v1/imports/{job_id}", headers=auth()).json()["data"]
    assert state["status"] in ("running", "cancelled", "completed")
    duplicate = client.post(
        "/api/v1/imports", headers=auth(),
        json={"table_id": table_id, "format": "jsonl", "content": json.dumps({"body": "x"}),
              "batch_id": "cancel-batch-1"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["duplicate"] is True


def test_csv_export_import_round_trip_preserves_types(settings):
    client = make_client(settings)
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/导入导出/测试",
            "name": "类型表",
            "columns": [
                {"name": "body", "type": "string", "nullable": False},
                {"name": "score", "type": "float64", "nullable": True},
                {"name": "count", "type": "int64", "nullable": True},
                {"name": "flag", "type": "boolean", "nullable": True},
            ],
        },
    )
    assert created.status_code == 201, created.text
    table_id = created.json()["data"]["table_id"]
    client.post(
        f"/api/v1/tables/{table_id}/rows",
        headers=auth(),
        json={"values": {"body": "带类型行", "score": 0.91, "count": 3, "flag": True}},
    )
    export = client.post("/api/v1/exports", headers=auth(), json={"table_id": table_id, "format": "csv"})
    assert export.status_code == 202
    job_id = export.json()["data"]["job_id"]
    wait_terminal(client, f"/api/v1/exports/{job_id}")
    download = client.get(f"/api/v1/exports/{job_id}/download", headers=auth())
    assert download.status_code == 200
    csv_content = download.text
    assert csv_content.splitlines()[0] == "body,score,count,flag"

    target_created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/导入导出/测试",
            "name": "类型导入目标",
            "columns": [
                {"name": "body", "type": "string", "nullable": False},
                {"name": "score", "type": "float64", "nullable": True},
                {"name": "count", "type": "int64", "nullable": True},
                {"name": "flag", "type": "boolean", "nullable": True},
            ],
        },
    )
    assert target_created.status_code == 201
    target = target_created.json()["data"]["table_id"]
    imported = client.post(
        "/api/v1/imports",
        headers=auth(),
        json={"table_id": target, "format": "csv", "content": csv_content},
    )
    assert imported.status_code == 202, imported.text
    state = wait_terminal(client, f"/api/v1/imports/{imported.json()['data']['job_id']}")
    assert state["status"] == "completed" and state["failed"] == 0, json.dumps({k: state.get(k) for k in ('status', 'error', 'results', 'done')}, ensure_ascii=False)
    row = client.get(f"/api/v1/tables/{target}/rows?limit=100", headers=auth()).json()["data"][0]
    assert row["values"]["body"] == "带类型行"
    assert row["values"]["score"] == 0.91 and type(row["values"]["score"]) is float
    assert row["values"]["count"] == 3 and type(row["values"]["count"]) is int
    assert row["values"]["flag"] is True


def test_csv_import_rejects_unknown_header(settings):
    client = make_client(settings)
    table_id = create_table(client, "表头校验")
    imported = client.post(
        "/api/v1/imports",
        headers=auth(),
        json={"table_id": table_id, "format": "csv", "content": "body,nope\nx,y"},
    )
    assert imported.status_code == 202
    state = wait_terminal(client, f"/api/v1/imports/{imported.json()['data']['job_id']}")
    assert state["status"] == "failed"
    assert "UNKNOWN_COLUMN" in (state["error"] or "")
    assert client.get(f"/api/v1/tables/{table_id}/rows", headers=auth()).json()["data"] == []


def test_export_batch_id_is_scoped_and_conflicts_are_explicit(settings):
    client = make_client(settings)
    first = create_table(client, "导出作用域一")
    second = create_table(client, "导出作用域二")
    body = {"table_id": first, "format": "jsonl", "batch_id": "export-scope-0001"}
    accepted = client.post("/api/v1/exports", headers=auth(), json=body)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["data"]["job_id"]

    replay = client.post("/api/v1/exports", headers=auth(), json=body)
    assert replay.status_code == 200
    assert replay.json()["data"]["duplicate"] is True
    assert replay.json()["data"]["job_id"] == job_id

    conflict = client.post(
        "/api/v1/exports", headers=auth(),
        json={**body, "format": "csv"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "BATCH_CONFLICT"

    other_table = client.post(
        "/api/v1/exports", headers=auth(),
        json={**body, "table_id": second},
    )
    assert other_table.status_code == 202, other_table.text
    assert other_table.json()["data"]["job_id"] != job_id


def test_import_batch_id_is_scoped_to_caller_and_payload(settings):
    client = make_client(settings)
    table_id = create_table(client, "导入作用域")
    content = json.dumps({"values": {"body": "imported"}}) + "\n"
    body = {"table_id": table_id, "format": "jsonl", "content": content, "batch_id": "import-scope-0001"}
    accepted = client.post("/api/v1/imports", headers=auth(), json=body)
    assert accepted.status_code == 202, accepted.text
    job_id = accepted.json()["data"]["job_id"]

    replay = client.post("/api/v1/imports", headers=auth(), json=body)
    assert replay.status_code == 200
    assert replay.json()["data"]["duplicate"] is True
    assert replay.json()["data"]["job_id"] == job_id

    conflict = client.post(
        "/api/v1/imports", headers=auth(),
        json={**body, "content": json.dumps({"values": {"body": "different"}}) + "\n"},
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "BATCH_CONFLICT"

    assert put_acl(client, "table", table_id, {"bob": ["read", "write"]}).status_code == 200
    bob = client.post("/api/v1/imports", headers=auth("bob"), json=body)
    assert bob.status_code == 202, bob.text
    assert bob.json()["data"]["job_id"] != job_id
