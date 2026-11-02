"""Issue #81: atomic derived-index rebuild endpoint (recovery flow)."""

import sqlite3

import pytest

from .conftest import auth


def seed(client):
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/数学/索引重建",
            "name": "索引重建验收",
            "columns": [
                {"name": "title", "type": "string", "nullable": False},
                {"name": "confidence", "type": "float64"},
            ],
        },
    )
    assert created.status_code == 201, created.text
    tid = created.json()["data"]["table_id"]
    for index in range(3):
        row = client.post(
            f"/api/v1/tables/{tid}/rows",
            headers=auth(),
            json={"values": {"title": f"积分 交换 {index}", "confidence": 0.1 * index}},
        )
        assert row.status_code == 201, row.text
    return tid


def rebuild(client, tid, dry_run, token="admin"):
    return client.post(
        f"/api/v1/tables/{tid}/maintenance/rebuild-indexes",
        headers=auth(token),
        json={"dry_run": dry_run},
    )


def counts(database):
    with sqlite3.connect(database) as db:
        return {
            "data": db.execute("SELECT COUNT(*) FROM data WHERE _deleted_at IS NULL").fetchone()[0],
            "legacy": db.execute("SELECT COUNT(*) FROM __xl_fts").fetchone()[0],
            "cjk": db.execute("SELECT COUNT(*) FROM __xl_fts_cjk").fetchone()[0],
            "map": db.execute("SELECT COUNT(*) FROM __xl_fts_map").fetchone()[0],
        }


def test_rebuild_indexes_requires_admin_scope(library):
    tid = seed(library)
    denied = rebuild(library, tid, False, token="alice")
    assert denied.status_code == 403


def test_rebuild_indexes_dry_run_reports_consistency_without_changing(library, tmp_path):
    tid = seed(library)
    database = tmp_path / "tables" / f"{tid}.xl"
    before = counts(database)
    response = rebuild(library, tid, True)
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["dry_run"] is True and body["consistent"] is True
    assert body["before"]["data_rows"] == before["data"] == 3
    assert counts(database) == before


def test_rebuild_indexes_repairs_corrupted_and_missing_index(library, tmp_path):
    tid = seed(library)
    database = tmp_path / "tables" / f"{tid}.xl"
    with sqlite3.connect(database) as db:
        db.execute("DELETE FROM __xl_fts WHERE rowid IN (SELECT rowid FROM __xl_fts LIMIT 1)")
        db.execute("DELETE FROM __xl_fts_cjk WHERE rowid IN (SELECT rowid FROM __xl_fts_cjk LIMIT 1)")
        db.execute("DROP TABLE __xl_fts_map")
        db.commit()

    dry = rebuild(library, tid, True)
    assert dry.status_code == 200, dry.text
    assert dry.json()["data"]["consistent"] is False

    applied = rebuild(library, tid, False)
    assert applied.status_code == 200, applied.text
    data = applied.json()["data"]
    assert data["rebuilt"] == 3
    assert data["after"] == {
        "data_rows": 3, "legacy_rows": 3, "cjk_rows": 3, "mapping_rows": 3,
    }
    assert counts(database) == {"data": 3, "legacy": 3, "cjk": 3, "map": 3}
    hits = library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"})
    assert hits.status_code == 200, hits.text
    assert len(hits.json()["data"]) == 3


def test_rebuild_indexes_is_atomic_on_failure(library, tmp_path, monkeypatch):
    tid = seed(library)
    database = tmp_path / "tables" / f"{tid}.xl"
    before = counts(database)
    import xeonolab.library as library_module

    def fail_index(*_args, **_kwargs):
        raise RuntimeError("injected rebuild failure")

    monkeypatch.setattr(library_module, "append_cjk_row", fail_index)
    with pytest.raises(RuntimeError, match="injected rebuild failure"):
        rebuild(library, tid, False)
    monkeypatch.undo()

    assert counts(database) == before
    hits = library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"})
    assert hits.status_code == 200, hits.text
    assert len(hits.json()["data"]) == 3
