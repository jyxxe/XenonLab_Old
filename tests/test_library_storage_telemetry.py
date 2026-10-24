"""Issue #95 follow-up: read-only growth metrics for storage maintenance."""

import sqlite3

from .conftest import auth


def test_storage_telemetry_reports_changes_and_object_counts(library, tmp_path):
    created = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/数学/分析", "name": "指标验收", "columns": [{"name": "title", "type": "string"}]},
    )
    assert created.status_code == 201, created.text
    tid = created.json()["data"]["table_id"]
    row = library.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"title": "v1"}})
    assert row.status_code == 201, row.text
    row_id = row.json()["data"]["_row_id"]
    referenced, orphan = "a" * 64, "b" * 64
    database = tmp_path / "tables" / f"{tid}.xl"
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
            ("att-1", row_id, referenced, "proof.png", "image/png", 7, "2020-01-01T00:00:00Z", "{}"),
        )
    for digest, size in ((referenced, 7), (orphan, 5)):
        path = tmp_path / "objects" / "sha256" / digest[:2] / digest[2:4] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)

    denied = library.get("/api/v1/storage/telemetry", headers=auth())
    assert denied.status_code == 403
    response = library.get("/api/v1/storage/telemetry", headers=auth("admin"))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    table = next(item for item in data["tables"] if item["table_id"] == tid)
    assert table["changes_rows"] >= 1
    assert table["attachment_refs"] == 1
    assert table["attachment_bytes"] == 7
    assert data["totals"]["changes_rows"] >= 1
    assert data["attachment_objects"]["total"] == 2
    assert data["attachment_objects"]["referenced"] == 1
    assert data["attachment_objects"]["unreferenced"] == 1
    assert data["attachment_objects"]["bytes_total"] == 12
    assert data["attachment_objects"]["bytes_unreferenced"] == 5
