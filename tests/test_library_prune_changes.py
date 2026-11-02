"""Issue #95: explicit, bounded __xl_changes retention endpoint."""

import sqlite3

from .conftest import auth


def seed_changes(client, tmp_path):
    result = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/数学/分析",
            "name": "保留窗口验收",
            "columns": [
                {"name": "title", "type": "string", "nullable": False},
                {"name": "confidence", "type": "float64"},
            ],
        },
    )
    assert result.status_code == 201, result.text
    tid = result.json()["data"]["table_id"]
    created = client.post(
        f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"title": "v1", "confidence": 0.1}}
    )
    assert created.status_code == 201, created.text
    row_id = created.json()["data"]["_row_id"]
    for index in range(2, 5):
        current = client.get(f"/api/v1/tables/{tid}/rows/{row_id}", headers=auth())
        patched = client.patch(
            f"/api/v1/tables/{tid}/rows/{row_id}",
            headers=auth(**{"If-Match": current.headers["ETag"]}),
            json={"values": {"title": f"v{index}"}},
        )
        assert patched.status_code == 200, patched.text
    database = tmp_path / "tables" / f"{tid}.xl"
    with sqlite3.connect(database) as db:
        db.execute("UPDATE __xl_changes SET created_at='2020-01-01T00:00:00Z'")
        total = db.execute("SELECT COUNT(*) FROM __xl_changes").fetchone()[0]
    return tid, row_id, database, total


def test_prune_changes_requires_admin_scope(library, tmp_path):
    tid, _, _, _ = seed_changes(library, tmp_path)
    denied = library.post(f"/api/v1/tables/{tid}/maintenance/prune-changes", headers=auth(), json={})
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] in {"FORBIDDEN", "XL_FORBIDDEN"}


def test_prune_changes_dry_run_reports_candidates_without_deleting(library, tmp_path):
    tid, _, database, total = seed_changes(library, tmp_path)
    response = library.post(
        f"/api/v1/tables/{tid}/maintenance/prune-changes",
        headers=auth("admin"),
        json={"dry_run": True, "older_than_days": 30, "limit": 100, "keep_recent": 2},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["dry_run"] is True and body["deleted"] == 0
    assert 0 < body["candidates"] <= total - 1
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT COUNT(*) FROM __xl_changes").fetchone()[0] == total


def test_prune_changes_deletes_only_bounded_candidates_and_keeps_latest(library, tmp_path):
    tid, row_id, database, total = seed_changes(library, tmp_path)
    response = library.post(
        f"/api/v1/tables/{tid}/maintenance/prune-changes",
        headers=auth("admin"),
        json={"dry_run": False, "older_than_days": 30, "limit": 1, "keep_recent": 1},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["deleted"] == 1
    with sqlite3.connect(database) as db:
        remaining = db.execute("SELECT COUNT(*) FROM __xl_changes").fetchone()[0]
        latest = db.execute(
            "SELECT MAX(revision) FROM __xl_changes WHERE row_id=?", (row_id,)
        ).fetchone()[0]
    assert remaining == total - 1
    assert latest == 4


def test_prune_changes_vacuum_reclaims_free_pages_after_real_delete(library, tmp_path):
    tid, row_id, database, total = seed_changes(library, tmp_path)
    before = database.stat().st_size
    response = library.post(
        f"/api/v1/tables/{tid}/maintenance/prune-changes",
        headers=auth("admin"),
        json={
            "dry_run": False,
            "older_than_days": 30,
            "limit": 3,
            "keep_recent": 1,
            "vacuum": True,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["deleted"] == 3
    assert body["vacuumed"] is True
    assert body["file_bytes_before"] == before
    assert 0 < body["file_bytes_after"] <= body["file_bytes_before"]
    assert "vacuum_error" not in body
    with sqlite3.connect(database) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert library.get(f"/api/v1/tables/{tid}/rows/{row_id}", headers=auth()).status_code == 200


def test_prune_changes_dry_run_never_vacuums(library, tmp_path):
    tid, _, database, _ = seed_changes(library, tmp_path)
    before = database.stat().st_size
    response = library.post(
        f"/api/v1/tables/{tid}/maintenance/prune-changes",
        headers=auth("admin"),
        json={"dry_run": True, "older_than_days": 30, "vacuum": True},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["vacuumed"] is False
    assert body["file_bytes_after"] == before
    assert body["deleted"] == 0


def test_prune_changes_rejects_invalid_bounds(library):
    tid = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/数学/分析", "name": "bounds", "columns": [{"name": "title", "type": "string"}]},
    ).json()["data"]["table_id"]
    for payload in (
        {"older_than_days": 0},
        {"older_than_days": 4000},
        {"limit": 0},
        {"keep_recent": 0},
    ):
        response = library.post(
            f"/api/v1/tables/{tid}/maintenance/prune-changes", headers=auth("admin"), json=payload
        )
        assert response.status_code == 422, payload
