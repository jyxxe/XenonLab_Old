"""Issue #95 (second increment): dry-run-first attachment object GC."""

import os
import sqlite3
import time

from .conftest import auth


def prepare_objects(library, tmp_path):
    created = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/数学/分析", "name": "附件回收验收", "columns": [{"name": "title", "type": "string"}]},
    )
    assert created.status_code == 201, created.text
    tid = created.json()["data"]["table_id"]
    row = library.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"title": "row"}})
    assert row.status_code == 201, row.text
    row_id = row.json()["data"]["_row_id"]
    referenced, orphan, fresh = "a" * 64, "b" * 64, "c" * 64
    database = tmp_path / "tables" / f"{tid}.xl"
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
            ("att-1", row_id, referenced, "proof.png", "image/png", 4, "2020-01-01T00:00:00Z", "{}"),
        )
    objects = tmp_path / "objects" / "sha256"
    paths = {}
    for digest, size in ((referenced, 4), (orphan, 5), (fresh, 6)):
        path = objects / digest[:2] / digest[2:4] / digest
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
        paths[digest] = path
    old = time.time() - 72 * 3600
    os.utime(paths[referenced], (old, old))
    os.utime(paths[orphan], (old, old))
    return paths


def test_attachment_gc_requires_admin_scope(library, tmp_path):
    prepare_objects(library, tmp_path)
    denied = library.post("/api/v1/storage/attachment-gc", headers=auth(), json={})
    assert denied.status_code == 403


def test_attachment_gc_dry_run_lists_only_old_unreferenced_objects(library, tmp_path):
    paths = prepare_objects(library, tmp_path)
    response = library.post(
        "/api/v1/storage/attachment-gc",
        headers=auth("admin"),
        json={"dry_run": True, "grace_hours": 24, "limit": 100},
    )
    assert response.status_code == 200, response.text
    body = response.json()["data"]
    assert body["dry_run"] is True and body["deleted"] == 0
    assert body["candidate_count"] == 1
    assert body["candidates"][0]["digest"] == "b" * 64
    assert body["confirm"].startswith("gc-")
    assert all(path.exists() for path in paths.values())


def test_attachment_gc_requires_confirmation_and_deletes_only_candidates(library, tmp_path):
    paths = prepare_objects(library, tmp_path)
    dry = library.post(
        "/api/v1/storage/attachment-gc",
        headers=auth("admin"),
        json={"dry_run": True, "grace_hours": 24, "limit": 100},
    ).json()["data"]
    refused = library.post(
        "/api/v1/storage/attachment-gc",
        headers=auth("admin"),
        json={"dry_run": False, "grace_hours": 24, "limit": 100},
    )
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "GC_CONFIRMATION_REQUIRED"
    confirmed = library.post(
        "/api/v1/storage/attachment-gc",
        headers=auth("admin"),
        json={"dry_run": False, "grace_hours": 24, "limit": 100, "confirm": dry["confirm"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    body = confirmed.json()["data"]
    assert body["deleted"] == 1
    assert not paths["b" * 64].exists()
    assert paths["a" * 64].exists(), "referenced object is kept"
    assert paths["c" * 64].exists(), "object inside the grace window is kept"


def test_attachment_gc_rejects_invalid_bounds(library):
    for payload in ({"grace_hours": 0}, {"grace_hours": 721}, {"limit": 0}, {"confirm": "short"}):
        response = library.post("/api/v1/storage/attachment-gc", headers=auth("admin"), json=payload)
        assert response.status_code == 422, payload
