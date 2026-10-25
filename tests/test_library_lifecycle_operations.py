"""Issue #81 second increment: durable table-lifecycle operation journal."""

import shutil
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4

from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth

OP_COLUMNS = ("id", "kind", "table_id", "state", "payload", "created_at", "updated_at", "error")


def create_empty_table(library, name="操作日志", path="/操作日志"):
    created = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": path, "name": name, "columns": [{"name": "title", "type": "string"}]},
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["table_id"]


def table_meta(root, table_id):
    with sqlite3.connect(root / "tables" / f"{table_id}.xl") as db:
        db.row_factory = sqlite3.Row
        return dict(db.execute("SELECT * FROM __xl_meta").fetchone())


def delete_table(library, table_id):
    schema = library.get(f"/api/v1/tables/{table_id}/schema", headers=auth("admin"))
    assert schema.status_code == 200, schema.text
    deleted = library.delete(
        f"/api/v1/tables/{table_id}", headers=auth("admin", **{"If-Match": schema.headers["ETag"]})
    )
    assert deleted.status_code == 200, deleted.text


def operations(root):
    with sqlite3.connect(root / "catalogue.sqlite") as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute("SELECT * FROM operations ORDER BY created_at")]


def add_pending_operation(root, kind, table_id, payload="{}", created_at=None):
    created_at = created_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(root / "catalogue.sqlite") as db:
        db.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,?,NULL)",
            (str(uuid4()), kind, table_id, "pending", payload, created_at, created_at),
        )


def add_completed_operation(root, kind, table_id, created_at):
    with sqlite3.connect(root / "catalogue.sqlite") as db:
        db.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,?,NULL)",
            (str(uuid4()), kind, table_id, "completed", "{}", created_at, created_at),
        )


def restart(settings):
    return TestClient(create_app("xelibrary", settings))


def test_create_and_delete_record_completed_operations(library, tmp_path):
    table_id = create_empty_table(library)
    records = operations(tmp_path)
    created = [row for row in records if row["kind"] == "table.create"]
    assert len(created) == 1
    assert created[0]["table_id"] == table_id
    assert created[0]["state"] == "completed"

    delete_table(library, table_id)
    records = operations(tmp_path)
    deleted = [row for row in records if row["kind"] == "table.delete"]
    assert len(deleted) == 1
    assert deleted[0]["table_id"] == table_id
    assert deleted[0]["state"] == "completed"


def test_failed_create_aborts_intent_and_leaves_no_file(library, tmp_path):
    table_id = create_empty_table(library, name="重名")
    before = len(list((tmp_path / "tables").glob("*.xl")))
    duplicate = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/操作日志", "name": "重名", "columns": [{"name": "title", "type": "string"}]},
    )
    assert duplicate.status_code == 409
    records = operations(tmp_path)
    aborted = [row for row in records if row["kind"] == "table.create" and row["state"] == "aborted"]
    assert len(aborted) == 1
    assert aborted[0]["table_id"] != table_id
    assert len(list((tmp_path / "tables").glob("*.xl"))) == before


def test_recovery_removes_unpublished_create_file(library, settings, tmp_path):
    published = create_empty_table(library)
    orphan_id = str(uuid4())
    source = tmp_path / "tables" / f"{published}.xl"
    orphan = tmp_path / "tables" / f"{orphan_id}.xl"
    shutil.copyfile(source, orphan)
    with sqlite3.connect(orphan) as db:
        db.execute("UPDATE __xl_meta SET table_id=?", (orphan_id,))
    add_pending_operation(tmp_path, "table.create", orphan_id, '{"path": "/操作日志"}')

    restart(settings)

    assert not orphan.exists()
    record = next(row for row in operations(tmp_path) if row["table_id"] == orphan_id)
    assert record["state"] == "completed"
    assert "removed unpublished" in record["error"]
    assert (tmp_path / "tables" / f"{published}.xl").exists()
    with sqlite3.connect(tmp_path / "catalogue.sqlite") as db:
        recovered = db.execute(
            "SELECT 1 FROM audit WHERE event='operation.recovered' AND resource=?",
            (record["id"],),
        ).fetchone()
    assert recovered is not None


def test_recovery_keeps_published_create_and_completes_intent(library, settings, tmp_path):
    table_id = create_empty_table(library)
    add_pending_operation(tmp_path, "table.create", table_id, '{"path": "/操作日志"}')

    restart(settings)

    assert (tmp_path / "tables" / f"{table_id}.xl").is_file()
    records = [row for row in operations(tmp_path) if row["table_id"] == table_id]
    assert {row["state"] for row in records} == {"completed"}


def test_recovery_aborts_delete_intent_when_catalogue_kept_the_table(library, settings, tmp_path):
    table_id = create_empty_table(library)
    add_pending_operation(tmp_path, "table.delete", table_id)
    (tmp_path / "tables" / f"{table_id}.xl").rename(tmp_path / "tables" / f".{table_id}.xl.deleting")

    restart(settings)

    assert (tmp_path / "tables" / f"{table_id}.xl").is_file()
    record = next(row for row in operations(tmp_path) if row["table_id"] == table_id and row["kind"] == "table.delete")
    assert record["state"] == "aborted"
    assert "rolled back" in record["error"]
    with sqlite3.connect(tmp_path / "catalogue.sqlite") as db:
        assert db.execute("SELECT 1 FROM tables WHERE id=?", (table_id,)).fetchone() is not None


def test_recovery_finishes_delete_intent_after_catalogue_commit(library, settings, tmp_path):
    table_id = create_empty_table(library, name="待删除遗留")
    delete_table(library, table_id)
    leftover = tmp_path / "tables" / f".{table_id}.xl.deleting"
    shutil.copyfile(tmp_path / "tables" / f"{create_empty_table(library, name='模板')}.xl", leftover)
    add_pending_operation(tmp_path, "table.delete", table_id)

    restart(settings)

    assert not leftover.exists()
    record = next(row for row in operations(tmp_path) if row["table_id"] == table_id and row["kind"] == "table.delete")
    assert record["state"] == "completed"


def test_recovery_prunes_old_completed_operations(library, settings, tmp_path):
    old_id, recent_id = str(uuid4()), str(uuid4())
    add_completed_operation(tmp_path, "table.create", old_id, "2020-01-01T00:00:00Z")
    add_completed_operation(tmp_path, "table.create", recent_id, "2099-01-01T00:00:00Z")

    restart(settings)

    table_ids = {row["table_id"] for row in operations(tmp_path)}
    assert old_id not in table_ids
    assert recent_id in table_ids


def test_doctor_reports_pending_operation(library, tmp_path):
    table_id = create_empty_table(library)
    add_pending_operation(tmp_path, "table.delete", table_id)

    response = library.get("/api/v1/maintenance/consistency", headers=auth("admin"))
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    issue = next(item for item in data["issues"] if item["kind"] == "incomplete_lifecycle_operation")
    assert issue["severity"] == "warning"
    assert issue["table_id"] == table_id
    assert data["counts"]["pending_operations"] == 1
    assert data["ok"] is True


def test_move_records_completed_operation(library, tmp_path):
    table_id = create_empty_table(library)
    schema = library.get(f"/api/v1/tables/{table_id}/schema", headers=auth("admin"))
    moved = library.post(
        f"/api/v1/tables/{table_id}/move",
        headers=auth(**{"If-Match": schema.headers["ETag"]}),
        json={"path": "/移动后"},
    )
    assert moved.status_code == 200, moved.text
    records = [row for row in operations(tmp_path) if row["kind"] == "table.move"]
    assert len(records) == 1
    assert records[0]["table_id"] == table_id
    assert records[0]["state"] == "completed"
    assert table_meta(tmp_path, table_id)["logical_path"] == "/移动后"


def test_failed_move_aborts_intent(library, tmp_path):
    table_id = create_empty_table(library, name="冲突名")
    create_empty_table(library, name="冲突名", path="/目标")
    schema = library.get(f"/api/v1/tables/{table_id}/schema", headers=auth("admin"))
    moved = library.post(
        f"/api/v1/tables/{table_id}/move",
        headers=auth(**{"If-Match": schema.headers["ETag"]}),
        json={"path": "/目标"},
    )
    assert moved.status_code == 409
    records = [row for row in operations(tmp_path) if row["kind"] == "table.move"]
    assert len(records) == 1
    assert records[0]["state"] == "aborted"
    assert table_meta(tmp_path, table_id)["logical_path"] == "/操作日志"


def test_recovery_reconciles_interrupted_move(library, settings, tmp_path):
    table_id = create_empty_table(library)
    before = table_meta(tmp_path, table_id)["table_revision"]
    with sqlite3.connect(tmp_path / "catalogue.sqlite") as db:
        db.execute("UPDATE tables SET path=? WHERE id=?", ("/已移动", table_id))
    add_pending_operation(tmp_path, "table.move", table_id, '{"from": "/操作日志", "to": "/已移动"}')

    restart(settings)

    meta = table_meta(tmp_path, table_id)
    assert meta["logical_path"] == "/已移动"
    assert meta["table_revision"] == before + 1
    record = next(row for row in operations(tmp_path) if row["kind"] == "table.move")
    assert record["state"] == "completed"
    assert "reconciled" in record["error"]


def test_recovery_aborts_uncommitted_move(library, settings, tmp_path):
    table_id = create_empty_table(library)
    add_pending_operation(tmp_path, "table.move", table_id, '{"from": "/操作日志", "to": "/未提交"}')

    restart(settings)

    assert table_meta(tmp_path, table_id)["logical_path"] == "/操作日志"
    record = next(row for row in operations(tmp_path) if row["kind"] == "table.move")
    assert record["state"] == "aborted"
    assert "did not commit" in record["error"]
