"""Issue #81 fourth increment: explicit dry-run-first repair plans."""

import sqlite3
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .conftest import auth
from .test_library_lifecycle_operations import create_empty_table, table_meta


def repair(library, token="admin", **body):
    return library.post("/api/v1/maintenance/consistency/repairs", headers=auth(token), json=body)


def apply_plan(library, plan_id, token="admin"):
    return repair(library, token=token, dry_run=False, plan_id=plan_id)


def consistency(library):
    response = library.get("/api/v1/maintenance/consistency", headers=auth("admin"))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def make_tombstone(root, table_id):
    (root / "tables" / f"{table_id}.xl").rename(root / "tables" / f".{table_id}.xl.deleting")


def remove_catalogue_row(root, table_id):
    with sqlite3.connect(root / "catalogue.sqlite") as db:
        db.execute("DELETE FROM tables WHERE id=?", (table_id,))


def add_upload_row(root, table_id):
    with sqlite3.connect(root / "uploads.sqlite") as db:
        db.execute(
            "INSERT INTO uploads(upload_id,attachment_id,actor,table_id,row_id,filename,size,"
            "part_size,part_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(uuid4()), str(uuid4()), "alice", table_id, "row", "a.bin", 1, 1, 1, "2020-01-01T00:00:00Z"),
        )


def test_repairs_require_admin(library, tmp_path):
    table_id = create_empty_table(library)
    make_tombstone(tmp_path, table_id)
    assert repair(library, token="alice").status_code == 403


def test_dry_run_reports_actions_without_changing_state(library, tmp_path):
    table_id = create_empty_table(library)
    make_tombstone(tmp_path, table_id)
    response = repair(library)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["dry_run"] is True
    assert data["plan_id"]
    assert data["actions"] == [
        {"kind": "interrupted_table_delete", "table_id": table_id, "action": "restore"}
    ]
    assert data["counts"]["actions"] == 1
    assert (tmp_path / "tables" / f".{table_id}.xl.deleting").exists()
    assert not (tmp_path / "tables" / f"{table_id}.xl").exists()


def test_apply_restores_table_with_catalogue_row(library, tmp_path):
    table_id = create_empty_table(library)
    with sqlite3.connect(tmp_path / "tables" / f"{table_id}.xl") as db:
        db.execute("ALTER TABLE __xl_meta ADD COLUMN deleting INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE __xl_meta SET deleting=1")
    make_tombstone(tmp_path, table_id)

    plan = repair(library).json()["data"]
    applied = apply_plan(library, plan["plan_id"])
    assert applied.status_code == 200, applied.text
    assert applied.json()["data"]["applied"][0]["result"] == "restored"
    assert (tmp_path / "tables" / f"{table_id}.xl").is_file()
    assert not (tmp_path / "tables" / f".{table_id}.xl.deleting").exists()
    meta = table_meta(tmp_path, table_id)
    assert "deleting" in meta and meta["deleting"] == 0
    with sqlite3.connect(tmp_path / "catalogue.sqlite") as db:
        repaired = db.execute("SELECT 1 FROM audit WHERE event='maintenance.repaired'").fetchone()
    assert repaired is not None
    assert apply_plan(library, plan["plan_id"]).status_code == 409


def test_apply_finishes_committed_delete_and_cleans_uploads(library, tmp_path):
    table_id = create_empty_table(library)
    add_upload_row(tmp_path, table_id)
    make_tombstone(tmp_path, table_id)
    remove_catalogue_row(tmp_path, table_id)

    plan = repair(library).json()["data"]
    assert plan["actions"] == [
        {"kind": "interrupted_table_delete", "table_id": table_id, "action": "finish"}
    ]
    applied = apply_plan(library, plan["plan_id"])
    assert applied.status_code == 200, applied.text
    assert applied.json()["data"]["applied"][0]["result"] == "finished"
    assert not (tmp_path / "tables" / f".{table_id}.xl.deleting").exists()
    with sqlite3.connect(tmp_path / "uploads.sqlite") as db:
        assert db.execute("SELECT count(*) FROM uploads WHERE table_id=?", (table_id,)).fetchone()[0] == 0


def test_apply_reconciles_path_and_clears_deletion_marker(library, tmp_path):
    table_id = create_empty_table(library)
    before = table_meta(tmp_path, table_id)["table_revision"]
    with sqlite3.connect(tmp_path / "tables" / f"{table_id}.xl") as db:
        db.execute("ALTER TABLE __xl_meta ADD COLUMN deleting INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE __xl_meta SET deleting=1, logical_path='/drifted'")

    plan = repair(library).json()["data"]
    kinds = {action["kind"] for action in plan["actions"]}
    assert kinds == {"table_path_mismatch", "table_deletion_marker"}
    applied = apply_plan(library, plan["plan_id"])
    assert applied.status_code == 200, applied.text
    results = {item["kind"]: item["result"] for item in applied.json()["data"]["applied"]}
    assert results == {"table_deletion_marker": "cleared", "table_path_mismatch": "reconciled"}
    meta = table_meta(tmp_path, table_id)
    assert meta["logical_path"] == "/操作日志"
    assert meta["deleting"] == 0
    assert meta["table_revision"] == before + 1

    data = consistency(library)
    kinds = {issue["kind"] for issue in data["issues"]}
    assert "table_path_mismatch" not in kinds
    assert "table_deletion_marker" not in kinds


def test_stale_plan_is_rejected(library, tmp_path):
    table_id = create_empty_table(library)
    make_tombstone(tmp_path, table_id)
    plan = repair(library).json()["data"]
    (tmp_path / "tables" / f".{table_id}.xl.deleting").rename(tmp_path / "tables" / f"{table_id}.xl")

    stale = apply_plan(library, plan["plan_id"])
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "REPAIR_PLAN_STALE"


def test_expired_plan_is_rejected(library, tmp_path):
    table_id = create_empty_table(library)
    make_tombstone(tmp_path, table_id)
    plan = repair(library).json()["data"]
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    with sqlite3.connect(tmp_path / "catalogue.sqlite") as db:
        db.execute("UPDATE repair_plans SET created_at=? WHERE id=?", (old, plan["plan_id"]))

    expired = apply_plan(library, plan["plan_id"])
    assert expired.status_code == 409
    assert expired.json()["error"]["code"] == "REPAIR_PLAN_EXPIRED"
    assert (tmp_path / "tables" / f".{table_id}.xl.deleting").exists()


def test_apply_requires_plan_id(library):
    missing = repair(library, dry_run=False)
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "REPAIR_PLAN_REQUIRED"
    unknown = apply_plan(library, str(uuid4()))
    assert unknown.status_code == 404


def test_too_many_actions_are_refused(library, tmp_path):
    for _ in range(501):
        (tmp_path / "tables" / f".{uuid4()}.xl.deleting").write_bytes(b"")
    response = repair(library)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REPAIR_TOO_LARGE"
