import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from xeonolab.backup import validate_data_root
from xeonolab.services import create_app

from .conftest import auth, put_acl


def seed(client):
    result = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/数学/分析",
            "name": "一致收敛",
            "columns": [
                {"name": "title", "type": "string", "nullable": False},
                {"name": "confidence", "type": "float64"},
            ],
        },
    )
    assert result.status_code == 201, result.text
    tid = result.json()["data"]["table_id"]
    result = client.post(
        f"/api/v1/tables/{tid}/rows",
        headers=auth(),
        json={"values": {"title": "一致收敛", "confidence": 0.92}},
    )
    assert result.status_code == 201, result.text
    return tid, result.json()["data"]["_row_id"]


def create_empty_table(client, name="Temporary fixture"):
    result = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/验收临时数据",
            "name": name,
            "columns": [{"name": "body", "type": "text", "nullable": True}],
        },
    )
    assert result.status_code == 201, result.text
    return result.json()["data"]["table_id"]


def table_etag(client, tid):
    result = client.get(f"/api/v1/tables/{tid}/schema", headers=auth("admin"))
    assert result.status_code == 200, result.text
    return result.headers["ETag"]


def test_table_delete_requires_admin_fresh_revision_and_empty_rows(library):
    tid, rid = seed(library)
    url = f"/api/v1/tables/{tid}"
    headers = {"If-Match": table_etag(library, tid)}
    operation = library.get("/openapi.json").json()["paths"]["/api/v1/tables/{tid}"]["delete"]
    if_match = next(parameter for parameter in operation["parameters"] if parameter["name"] == "If-Match")
    assert if_match["in"] == "header" and if_match["required"] is True
    assert "xelibrary:admin" in operation["description"]
    assert {"403", "404", "409", "412", "503"} <= set(operation["responses"])
    schema = library.get(f"/api/v1/tables/{tid}/schema", headers=auth("admin"))
    assert "deleting" not in schema.json()["data"]

    assert library.delete(url, headers=auth(**headers)).status_code == 403
    assert library.delete(url, headers=auth("admin")).status_code == 412
    blocked = library.delete(url, headers=auth("admin", **headers))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "TABLE_NOT_EMPTY"
    assert library.get(f"/api/v1/tables/{tid}", headers=auth()).status_code == 200

    row_url = f"/api/v1/tables/{tid}/rows/{rid}"
    row = library.get(row_url, headers=auth("admin"))
    purged = library.delete(
        row_url + "?purge=true", headers=auth("admin", **{"If-Match": row.headers["ETag"]})
    )
    assert purged.status_code == 200, purged.text

    deleted = library.delete(url, headers=auth("admin", **{"If-Match": table_etag(library, tid)}))
    assert deleted.status_code == 200, deleted.text
    assert library.get(f"/api/v1/tables/{tid}", headers=auth("admin")).status_code == 404


def test_table_delete_removes_catalogue_acls_upload_sessions_and_file(library, settings):
    tid = create_empty_table(library)
    put_acl(library, "table", tid, {"bob": ["read"]})
    put_acl(library, "row", f"{tid}:temporary-row", {"bob": ["read"]})

    upload_id = str(uuid4())
    attachment_id = str(uuid4())
    upload_root = settings.data_dir / "objects" / ".uploads"
    upload_dir = upload_root / upload_id
    upload_dir.mkdir(parents=True)
    part_file = upload_dir / "fixture.part"
    part_file.write_bytes(b"temporary upload data")
    lock_file = upload_root / ".locks" / f"{upload_id}.lock"
    lock_file.touch()
    with sqlite3.connect(settings.data_dir / "uploads.sqlite") as db:
        db.execute(
            "INSERT INTO uploads(upload_id,attachment_id,actor,table_id,row_id,filename,size,part_size,part_count,created_at,cancelled) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,1)",
            (upload_id, attachment_id, "alice", tid, "temporary-row", "fixture.bin", 1, 1, 1, "now"),
        )

    table_file = settings.data_dir / "tables" / f"{tid}.xl"
    result = library.delete(f"/api/v1/tables/{tid}", headers=auth("admin", **{"If-Match": table_etag(library, tid)}))
    assert result.status_code == 200, result.text
    assert not table_file.exists()
    assert not list((settings.data_dir / "tables").glob(f".{tid}.xl.deleting"))
    assert not upload_dir.exists() and not lock_file.exists()
    with sqlite3.connect(settings.data_dir / "catalogue.sqlite") as db:
        assert db.execute("SELECT 1 FROM tables WHERE id=?", (tid,)).fetchone() is None
        assert db.execute("SELECT 1 FROM acl WHERE resource_id=?", (tid,)).fetchone() is None
        assert db.execute("SELECT 1 FROM acl WHERE resource_id LIKE ?", (tid + ":%",)).fetchone() is None
        assert db.execute(
            "SELECT 1 FROM audit WHERE resource=? AND event='table.deleted'", (tid,)
        ).fetchone() == (1,)
    with sqlite3.connect(settings.data_dir / "uploads.sqlite") as db:
        assert db.execute("SELECT 1 FROM uploads WHERE table_id=?", (tid,)).fetchone() is None


def test_table_delete_fails_closed_for_attachment_vectors_and_active_work(library, settings):
    tid, rid = seed(library)
    database = settings.data_dir / "tables" / f"{tid}.xl"
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
            (str(uuid4()), rid, "a" * 64, "fixture.bin", "application/octet-stream", 1, "now", "{}"),
        )
    result = library.delete(
        f"/api/v1/tables/{tid}", headers=auth("admin", **{"If-Match": table_etag(library, tid)})
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "TABLE_HAS_ATTACHMENTS"

    empty_tid = create_empty_table(library)
    empty_file = settings.data_dir / "tables" / f"{empty_tid}.xl"
    with sqlite3.connect(empty_file) as db:
        db.execute(
            "CREATE TABLE __xl_vectors(row_id TEXT,revision INTEGER,chunk_id TEXT PRIMARY KEY,column_name TEXT,body TEXT,vector TEXT,model_revision TEXT,chunker_version TEXT)"
        )
        db.execute("INSERT INTO __xl_vectors VALUES(?,?,?,?,?,?,?,?)", ("orphan", 1, "chunk", "body", "x", "[]", "r1", "v1"))
    vector_block = library.delete(
        f"/api/v1/tables/{empty_tid}", headers=auth("admin", **{"If-Match": table_etag(library, empty_tid)})
    )
    assert vector_block.status_code == 409
    assert vector_block.json()["error"]["code"] == "TABLE_HAS_VECTORS"

    with sqlite3.connect(empty_file) as db:
        db.execute("DELETE FROM __xl_vectors")
    from xeonolab.jobs import JobStore

    jobs = JobStore(settings.data_dir / "catalogue.sqlite")
    jobs.create("queued-table-delete", "admin", empty_tid)
    job_block = library.delete(
        f"/api/v1/tables/{empty_tid}", headers=auth("admin", **{"If-Match": table_etag(library, empty_tid)})
    )
    assert job_block.status_code == 409
    assert job_block.json()["error"]["code"] == "TABLE_INDEX_ACTIVE"


def test_table_delete_fails_closed_for_active_upload(library, settings):
    tid = create_empty_table(library)
    upload_id = str(uuid4())
    with sqlite3.connect(settings.data_dir / "uploads.sqlite") as db:
        db.execute(
            "INSERT INTO uploads(upload_id,attachment_id,actor,table_id,row_id,filename,size,part_size,part_count,created_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (upload_id, str(uuid4()), "alice", tid, "missing-row", "fixture.bin", 1, 1, 1, "now"),
        )
    result = library.delete(
        f"/api/v1/tables/{tid}", headers=auth("admin", **{"If-Match": table_etag(library, tid)})
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "TABLE_UPLOAD_ACTIVE"
    assert library.get(f"/api/v1/tables/{tid}", headers=auth("admin")).status_code == 200


def test_table_delete_serializes_row_and_upload_start_across_rename(library, settings, monkeypatch):
    tid = create_empty_table(library, "Delete race fixture")
    assert table_etag(library, tid)
    assert validate_data_root(settings.data_dir)[0]["id"] == tid
    table_path = settings.data_dir / "tables" / f"{tid}.xl"
    entered_rename, continue_rename = threading.Event(), threading.Event()
    original_replace = __import__("xeonolab.library", fromlist=["os"]).os.replace

    def blocked_replace(source, destination):
        if source == table_path:
            entered_rename.set()
            assert continue_rename.wait(timeout=5)
        return original_replace(source, destination)

    monkeypatch.setattr("xeonolab.library.os.replace", blocked_replace)
    with ThreadPoolExecutor(max_workers=4) as pool:
        deletion = pool.submit(
            library.delete,
            f"/api/v1/tables/{tid}",
            headers=auth("admin", **{"If-Match": table_etag(library, tid)}),
        )
        assert entered_rename.wait(timeout=5)
        write = pool.submit(
            library.post,
            f"/api/v1/tables/{tid}/rows",
            headers=auth(),
            json={"values": {"body": "must not land"}},
        )
        upload = pool.submit(
            library.post,
            "/api/v1/uploads",
            headers=auth(),
            json={"table_id": tid, "row_id": "future-row", "filename": "x.txt", "size": 1},
        )
        read = pool.submit(library.get, f"/api/v1/tables/{tid}/schema", headers=auth())
        continue_rename.set()
        deleted = deletion.result(timeout=5)
        write = write.result(timeout=5)
        upload = upload.result(timeout=5)
        read = read.result(timeout=5)

    assert write.status_code == 404
    assert upload.status_code == 404
    assert read.status_code == 404
    assert deleted.status_code == 200, deleted.text
    assert not table_path.exists()
    with sqlite3.connect(settings.data_dir / "uploads.sqlite") as db:
        assert db.execute("SELECT 1 FROM uploads WHERE table_id=?", (tid,)).fetchone() is None
    assert not list((settings.data_dir / "tables").glob(f".{tid}.xl"))


def test_table_delete_recovers_interrupted_catalogue_and_file_steps(library, settings):
    restore_tid = create_empty_table(library, "Restore after interrupted delete")
    restore_target = settings.data_dir / "tables" / f"{restore_tid}.xl"
    restore_tombstone = settings.data_dir / "tables" / f".{restore_tid}.xl.deleting"
    restore_target.replace(restore_tombstone)
    unavailable = library.get(f"/api/v1/tables/{restore_tid}/schema", headers=auth("admin"))
    assert unavailable.status_code == 503
    assert not restore_target.exists() and restore_tombstone.exists()
    recovered = TestClient(create_app("xelibrary", settings))
    restored = recovered.get(f"/api/v1/tables/{restore_tid}", headers=auth("admin"))
    assert restored.status_code == 200
    assert "deleting" not in restored.json()["data"]
    assert restore_target.exists() and not restore_tombstone.exists()

    finish_tid = create_empty_table(library, "Finish interrupted delete")
    finish_target = settings.data_dir / "tables" / f"{finish_tid}.xl"
    finish_tombstone = settings.data_dir / "tables" / f".{finish_tid}.xl.deleting"
    finish_target.replace(finish_tombstone)
    with sqlite3.connect(settings.data_dir / "catalogue.sqlite") as db:
        db.execute("DELETE FROM tables WHERE id=?", (finish_tid,))
    recovered = TestClient(create_app("xelibrary", settings))
    assert recovered.get(f"/api/v1/tables/{finish_tid}", headers=auth("admin")).status_code == 404
    assert not finish_target.exists() and not finish_tombstone.exists()


def test_crud_revision_and_query(library, settings):
    tid, rid = seed(library)
    path = f"/api/v1/tables/{tid}/rows/{rid}"
    assert list(settings.data_dir.glob("tables/*.xl"))
    assert library.get(path).status_code == 401
    assert library.get(path, headers=auth("bob")).status_code == 403
    assert library.patch(path, headers=auth(), json={"values": {"confidence": 0.8}}).status_code == 412
    result = library.patch(
        path, headers=auth(**{"If-Match": '"revision:1"'}), json={"values": {"confidence": 0.85}}
    )
    assert result.status_code == 200 and result.headers["ETag"] == '"revision:2"'
    result = library.post(
        "/api/v1/query",
        headers=auth(),
        json={"expression": 'FROM "/数学/**" WHERE confidence >= 0.8 AND title = "一致收敛" LIMIT 5'},
    )
    assert len(result.json()["data"]) == 1
    assert (
        library.post("/api/v1/query", headers=auth("bob"), json={"expression": 'FROM "/**"'}).json()["data"]
        == []
    )
    assert library.delete(path, headers=auth(**{"If-Match": '"revision:2"'})).status_code == 200
    assert library.get(path, headers=auth()).status_code == 404
    history = library.get(path + "/revisions", headers=auth()).json()["data"]
    assert [r["_revision"] for r in history] == [1, 2, 3]


def test_concurrent_updates(library):
    tid, rid = seed(library)

    def update(value):
        return library.patch(
            f"/api/v1/tables/{tid}/rows/{rid}",
            headers=auth(**{"If-Match": '"revision:1"'}),
            json={"values": {"confidence": value}},
        ).status_code

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(update, [0.5, 0.6])) == [200, 412]


def test_acl_move_and_attachment(library):
    tid, rid = seed(library)
    assert (
        put_acl(
            library,
            "table",
            tid,
            {
                "alice": ["read", "write", "attach"],
                "bob": ["read"],
                "attach-only": ["attach"],
                "read-attach": ["read", "attach"],
            },
        ).status_code
        == 200
    )
    assert library.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("bob")).status_code == 200
    result = library.post(
        "/api/v1/attachments",
        params={"table_id": tid, "row_id": rid},
        headers=auth(),
        files={"upload": ("../../proof.pdf", b"%PDF-test", "application/pdf")},
    )
    assert result.status_code == 201, result.text
    aid = result.json()["data"]["attachment_id"]
    metadata = library.get("/api/v1/attachments/" + aid, params={"table_id": tid}, headers=auth("bob"))
    assert metadata.status_code == 200
    assert metadata.json()["data"]["filename"] == "proof.pdf"
    assert metadata.json()["data"]["row_id"] == rid
    assert library.get(
        f"/api/v1/tables/{tid}/rows/{rid}/attachments", headers=auth("bob")
    ).json()["data"][0]["attachment_id"] == aid
    library.app.state.settings.dev_tokens["reader"] = {
        "sub": "reader", "scope": "xelibrary:read", "agent_kind": "human"
    }
    denied_upload = library.post(
        "/api/v1/attachments",
        params={"table_id": tid, "row_id": rid},
        headers=auth("reader"),
        files={"upload": ("blocked.txt", b"no", "text/plain")},
    )
    assert denied_upload.status_code == 403
    assert (
        library.get(
            f"/api/v1/attachments/{aid}/content", params={"table_id": tid}, headers=auth("bob")
        ).content
        == b"%PDF-test"
    )
    second = library.post(
        f"/api/v1/tables/{tid}/rows",
        headers=auth(),
        json={"values": {"title": "第二条记录", "confidence": 0.5}},
    )
    rid2 = second.json()["data"]["_row_id"]
    library.app.state.settings.dev_tokens["attach-only"] = {
        "sub": "attach-only", "scope": "xelibrary:attach", "agent_kind": "human"
    }
    attach_only = library.post(
        f"/api/v1/tables/{tid}/rows/{rid2}/attachments",
        headers=auth("attach-only"),
        json={"attachment_id": aid},
    )
    assert attach_only.status_code == 403
    assert attach_only.json()["error"]["code"] == "FORBIDDEN"
    library.app.state.settings.dev_tokens["read-attach"] = {
        "sub": "read-attach", "scope": "xelibrary:read xelibrary:attach", "agent_kind": "human"
    }
    assert put_acl(
        library,
        "row",
        f"{tid}:{rid}",
        {"alice": ["read", "attach"], "bob": ["read"], "read-attach": ["attach"]},
    ).status_code == 200
    source_acl_denied = library.post(
        f"/api/v1/tables/{tid}/rows/{rid2}/attachments",
        headers=auth("read-attach"),
        json={"attachment_id": aid},
    )
    assert source_acl_denied.status_code == 403
    assert source_acl_denied.json()["error"]["code"] == "XL_FORBIDDEN"
    assert library.post(
        f"/api/v1/tables/{tid}/rows/{rid2}/attachments",
        headers=auth("bob"),
        json={"attachment_id": aid},
    ).status_code == 403
    linked = library.post(
        f"/api/v1/tables/{tid}/rows/{rid2}/attachments",
        headers=auth(),
        json={"attachment_id": aid},
    )
    assert linked.status_code == 201, linked.text
    linked_id = linked.json()["data"]["attachment_id"]
    assert library.get(
        f"/api/v1/tables/{tid}/rows/{rid2}/attachments", headers=auth()
    ).json()["data"][0]["attachment_id"] == linked_id
    assert library.get(
        f"/api/v1/attachments/{linked_id}/content", params={"table_id": tid}, headers=auth("bob")
    ).content == b"%PDF-test"
    assert put_acl(library, "row", f"{tid}:{rid2}", {"alice": ["read", "attach"]}).status_code == 200
    assert library.get(
        "/api/v1/attachments/" + linked_id, params={"table_id": tid}, headers=auth("bob")
    ).status_code == 403
    assert library.get(
        f"/api/v1/attachments/{linked_id}/content", params={"table_id": tid}, headers=auth("bob")
    ).status_code == 403
    assert library.delete(
        f"/api/v1/tables/{tid}/rows/{rid2}/attachments/{linked_id}", headers=auth()
    ).status_code == 200
    assert library.get(
        f"/api/v1/tables/{tid}/rows/{rid2}/attachments", headers=auth()
    ).json()["data"] == []
    assert library.get(
        f"/api/v1/attachments/{linked_id}/content", params={"table_id": tid}, headers=auth()
    ).status_code == 404
    assert library.get(
        f"/api/v1/attachments/{aid}/content", params={"table_id": tid}, headers=auth("bob")
    ).content == b"%PDF-test"
    meta = library.get(f"/api/v1/tables/{tid}", headers=auth())
    result = library.post(
        f"/api/v1/tables/{tid}/move",
        headers=auth(**{"If-Match": meta.headers["ETag"]}),
        json={"path": "/数学/实分析"},
    )
    assert result.status_code == 200
    assert result.json()["data"]["table_id"] == tid


def test_invalid_schema_and_types(library):
    tid, _ = seed(library)
    for values in [{"title": 1}, {"title": "x", "unknown": 2}, {}]:
        assert (
            library.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": values}).status_code
            == 422
        )
    assert (
        library.post(
            "/api/v1/query", headers=auth(), json={"expression": '__import__("os").system("id")'}
        ).status_code
        == 422
    )


def test_persistence(library, settings):
    tid, rid = seed(library)
    from fastapi.testclient import TestClient

    from xeonolab.services import create_app

    with TestClient(create_app("xelibrary", settings)) as restarted:
        assert restarted.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).status_code == 200


def test_idempotency_and_schema_migration(library):
    tid, rid = seed(library)
    headers = auth(**{"Idempotency-Key": "request-1"})
    body = {"values": {"title": "幂等请求", "confidence": 0.5}}
    first = library.post(f"/api/v1/tables/{tid}/rows", headers=headers, json=body)
    retry = library.post(f"/api/v1/tables/{tid}/rows", headers=headers, json=body)
    assert first.json()["data"]["_row_id"] == retry.json()["data"]["_row_id"]
    assert (
        library.post(
            f"/api/v1/tables/{tid}/rows", headers=headers, json={"values": {"title": "不同数据"}}
        ).status_code
        == 409
    )
    table = library.get(f"/api/v1/tables/{tid}", headers=auth())
    changed = library.patch(
        f"/api/v1/tables/{tid}/schema",
        headers=auth("admin", **{"If-Match": table.headers["ETag"]}),
        json={
            "columns": [
                {"name": "title", "type": "string", "nullable": False},
                {"name": "confidence", "type": "float64"},
                {"name": "language", "type": "string", "default_value": "zh"},
            ]
        },
    )
    assert changed.status_code == 200, changed.text
    assert (
        library.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).json()["data"]["values"]["language"]
        == "zh"
    )


def test_parent_acl_prevents_creation(library):
    put_acl(library, "path", "/数学", {"bob": ["read"]})
    result = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/数学/分析", "name": "private", "columns": [{"name": "title", "type": "string"}]},
    )
    assert result.status_code == 403, result.text


def test_restore_revision_and_admin_purge(library):
    tid, rid = seed(library)
    path = f"/api/v1/tables/{tid}/rows/{rid}"
    assert library.get(path + "/revisions/1", headers=auth("bob")).status_code == 403
    assert library.get(path + "/revisions/99", headers=auth()).status_code == 404
    assert library.delete(path, headers=auth(**{"If-Match": '"revision:1"'})).status_code == 200
    assert library.post(path + "/restore", headers=auth(), json={"revision": 1}).status_code == 412
    result = library.post(
        path + "/restore", headers=auth(**{"If-Match": '"revision:2"'}), json={"revision": 1}
    )
    assert result.status_code == 200
    assert result.json()["data"]["_revision"] == 3
    assert result.json()["data"]["_deleted_at"] is None
    assert len(library.get(path + "/revisions", headers=auth()).json()["data"]) == 3
    assert library.delete(path + "?purge=true", headers=auth()).status_code == 403
    assert library.delete(path + "?purge=true", headers=auth("admin")).status_code == 412
    assert (
        library.delete(
            path + "?purge=true", headers=auth("admin", **{"If-Match": '"revision:3"'})
        ).status_code
        == 200
    )
    assert library.get(path + "/revisions/1", headers=auth("admin")).status_code == 404


def test_deleted_rows_are_opt_in_and_still_acl_checked(library):
    tid, rid = seed(library)
    row_path = f"/api/v1/tables/{tid}/rows/{rid}"
    assert library.delete(row_path, headers=auth(**{"If-Match": '"revision:1"'})).status_code == 200

    rows_path = f"/api/v1/tables/{tid}/rows"
    assert library.get(rows_path, headers=auth()).json()["data"] == []
    assert library.get(rows_path + "?include_deleted=true", headers=auth()).json()["data"][0]["_deleted_at"]
    assert put_acl(library, "table", tid, {"bob": ["read"]}).status_code == 200
    assert put_acl(library, "row", f"{tid}:{rid}", {}).status_code == 200
    assert library.get(rows_path + "?include_deleted=true", headers=auth("bob")).json()["data"] == []

    assert put_acl(library, "row", f"{tid}:{rid}", {"bob": ["read"]}).status_code == 200
    visible = library.get(rows_path + "?include_deleted=true", headers=auth("bob"))
    assert [row["_row_id"] for row in visible.json()["data"]] == [rid]


def test_table_rows_sort_across_cursor_pages_with_stable_ties(library):
    table = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/排序验收",
            "name": "stable-sort",
            "columns": [
                {"name": "score", "type": "int64", "nullable": True},
                {"name": "title", "type": "string", "nullable": True},
            ],
        },
    )
    assert table.status_code == 201, table.text
    tid = table.json()["data"]["table_id"]
    created = []
    for score, title in [(2, "two-a"), (None, "null"), (1, "one"), (2, "two-b"), (3, "three")]:
        result = library.post(
            f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"score": score, "title": title}}
        )
        assert result.status_code == 201, result.text
        created.append(result.json()["data"])

    rows_path = f"/api/v1/tables/{tid}/rows"
    expected_asc = sorted(
        created,
        key=lambda row: (
            row["values"]["score"] is not None,
            row["values"]["score"] if row["values"]["score"] is not None else 0,
            row["_row_id"],
        ),
    )
    first = library.get(
        rows_path, params={"limit": 2, "sort_by": "score", "direction": "asc"}, headers=auth()
    )
    assert first.status_code == 200, first.text
    assert [row["_row_id"] for row in first.json()["data"]] == [row["_row_id"] for row in expected_asc[:2]]
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor and cursor.startswith("sort1.")

    second = library.get(
        rows_path,
        params={"limit": 2, "sort_by": "score", "direction": "asc", "cursor": cursor},
        headers=auth(),
    )
    assert second.status_code == 200, second.text
    third = library.get(
        rows_path,
        params={"limit": 2, "sort_by": "score", "direction": "asc", "cursor": second.json()["meta"]["next_cursor"]},
        headers=auth(),
    )
    assert third.status_code == 200, third.text
    actual = first.json()["data"] + second.json()["data"] + third.json()["data"]
    assert [row["_row_id"] for row in actual] == [row["_row_id"] for row in expected_asc]
    assert len({row["_row_id"] for row in actual}) == len(created)
    assert second.json()["meta"]["next_cursor"] and third.json()["meta"]["next_cursor"] is None

    expected_desc = sorted(
        created,
        key=lambda row: (
            row["values"]["score"] is None,
            -(row["values"]["score"] or 0),
            row["_row_id"],
        ),
    )
    descending = library.get(
        rows_path, params={"limit": 2, "sort_by": "score", "direction": "desc"}, headers=auth()
    )
    assert [row["_row_id"] for row in descending.json()["data"]] == [
        row["_row_id"] for row in expected_desc[:2]
    ]
    descending_rows = descending.json()["data"]
    descending_cursor = descending.json()["meta"]["next_cursor"]
    while descending_cursor:
        page = library.get(
            rows_path,
            params={
                "limit": 2,
                "sort_by": "score",
                "direction": "desc",
                "cursor": descending_cursor,
            },
            headers=auth(),
        )
        assert page.status_code == 200, page.text
        descending_rows.extend(page.json()["data"])
        descending_cursor = page.json()["meta"]["next_cursor"]
    assert [row["_row_id"] for row in descending_rows] == [row["_row_id"] for row in expected_desc]

    mismatch = library.get(
        rows_path,
        params={"limit": 2, "sort_by": "score", "direction": "desc", "cursor": cursor},
        headers=auth(),
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "INVALID_CURSOR"

    legacy = library.get(rows_path, params={"limit": 2}, headers=auth())
    assert [row["_row_id"] for row in legacy.json()["data"]] == sorted(row["_row_id"] for row in created)[:2]
    assert legacy.json()["meta"]["next_cursor"] == legacy.json()["data"][-1]["_row_id"]


def test_ascending_sort_pages_from_null_group_into_non_null_rows(library):
    table = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/排序验收",
            "name": "null-boundary-sort",
            "columns": [{"name": "score", "type": "int64", "nullable": True}],
        },
    )
    assert table.status_code == 201, table.text
    tid = table.json()["data"]["table_id"]
    created = []
    for score in (None, None, 1):
        result = library.post(
            f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"score": score}}
        )
        assert result.status_code == 201, result.text
        created.append(result.json()["data"])

    rows_path = f"/api/v1/tables/{tid}/rows"
    first = library.get(
        rows_path, params={"limit": 2, "sort_by": "score", "direction": "asc"}, headers=auth()
    )
    assert first.status_code == 200, first.text
    assert [row["values"]["score"] for row in first.json()["data"]] == [None, None]
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor

    second = library.get(
        rows_path,
        params={"limit": 2, "sort_by": "score", "direction": "asc", "cursor": cursor},
        headers=auth(),
    )
    assert second.status_code == 200, second.text
    assert [row["values"]["score"] for row in second.json()["data"]] == [1]
    assert second.json()["meta"]["next_cursor"] is None
    paged = first.json()["data"] + second.json()["data"]
    assert {row["_row_id"] for row in paged} == {row["_row_id"] for row in created}
    assert paged[-1]["values"]["score"] == 1


def test_sorted_row_cursors_preserve_acl_deleted_filter_and_reject_bad_sort(library):
    table = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/排序验收",
            "name": "acl-sort",
            "columns": [{"name": "score", "type": "int64", "nullable": False}],
        },
    )
    assert table.status_code == 201, table.text
    tid = table.json()["data"]["table_id"]
    rows = {}
    for score in (1, 2, 3, 4):
        result = library.post(
            f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"score": score}}
        )
        assert result.status_code == 201, result.text
        rows[score] = result.json()["data"]

    assert put_acl(library, "table", tid, {"bob": ["read"]}).status_code == 200
    assert put_acl(library, "row", f"{tid}:{rows[1]['_row_id']}", {"bob": []}).status_code == 200
    deleted_path = f"/api/v1/tables/{tid}/rows/{rows[2]['_row_id']}"
    deleted = library.delete(deleted_path, headers=auth("admin", **{"If-Match": '"revision:1"'}))
    assert deleted.status_code == 200, deleted.text

    rows_path = f"/api/v1/tables/{tid}/rows"
    first = library.get(
        rows_path,
        params={"limit": 1, "sort_by": "score", "direction": "asc"},
        headers=auth("bob"),
    )
    assert [row["values"]["score"] for row in first.json()["data"]] == [3]
    cursor = first.json()["meta"]["next_cursor"]
    mismatch = library.get(
        rows_path,
        params={"limit": 1, "sort_by": "score", "direction": "asc", "include_deleted": "true", "cursor": cursor},
        headers=auth("bob"),
    )
    assert mismatch.status_code == 422
    assert mismatch.json()["error"]["code"] == "INVALID_CURSOR"
    next_page = library.get(
        rows_path,
        params={"limit": 1, "sort_by": "score", "direction": "asc", "cursor": cursor},
        headers=auth("bob"),
    )
    assert [row["values"]["score"] for row in next_page.json()["data"]] == [4]
    assert next_page.json()["meta"]["next_cursor"] is None

    with_deleted = library.get(
        rows_path,
        params={"limit": 1, "sort_by": "score", "direction": "asc", "include_deleted": "true"},
        headers=auth("bob"),
    )
    assert [row["values"]["score"] for row in with_deleted.json()["data"]] == [2]

    invalid_field = library.get(
        rows_path, params={"sort_by": "score) OR 1=1 --", "direction": "asc"}, headers=auth("admin")
    )
    invalid_direction = library.get(
        rows_path, params={"sort_by": "score", "direction": "sideways"}, headers=auth("admin")
    )
    direction_without_field = library.get(rows_path, params={"direction": "asc"}, headers=auth("admin"))
    for result in (invalid_field, invalid_direction, direction_without_field):
        assert result.status_code == 422
        assert result.json()["error"]["code"] == "INVALID_SORT"


def test_row_write_refreshes_table_revision_for_schema_and_move(library):
    tid, rid = seed(library)
    row_path = f"/api/v1/tables/{tid}/rows/{rid}"
    assert library.patch(
        row_path, headers=auth(**{"If-Match": '"revision:1"'}), json={"values": {"confidence": 0.8}}
    ).status_code == 200

    table_path = f"/api/v1/tables/{tid}"
    table = library.get(table_path, headers=auth())
    changed = library.patch(
        table_path + "/schema",
        headers=auth("admin", **{"If-Match": table.headers["ETag"]}),
        json={"columns": [
            {"name": "title", "type": "string", "nullable": False},
            {"name": "confidence", "type": "float64"},
            {"name": "source", "type": "string", "default_value": "local"},
        ]},
    )
    assert changed.status_code == 200, changed.text
    table = library.get(table_path, headers=auth())
    moved = library.post(
        table_path + "/move",
        headers=auth(**{"If-Match": table.headers["ETag"]}),
        json={"path": "/数学/移动后"},
    )
    assert moved.status_code == 200, moved.text


def test_acl_inspection_and_audit_are_admin_only(library):
    tid, _ = seed(library)
    path = f"/api/v1/acl/table/{tid}"
    assert library.get(path, headers=auth()).status_code == 403
    assert library.get("/api/v1/audit", headers=auth()).status_code == 403
    assert library.get(path, headers=auth("admin")).json()["data"]["explicit"] is False
    assert library.put(
        path,
        headers=auth("alice", **{"If-Match": '"revision:0"'}),
        json={"grants": {"bob": ["read"]}},
    ).status_code == 403
    assert library.delete(path, headers=auth("alice", **{"If-Match": '"revision:0"'})).status_code == 403
    assert put_acl(library, "table", tid, {"bob": ["read"]}).status_code == 200
    assert library.get(path, headers=auth("admin")).json()["data"]["grants"] == {"bob": ["read"]}
    assert any(
        r["event"] == "acl.changed"
        for r in library.get("/api/v1/audit", headers=auth("admin")).json()["data"]
    )


def test_request_acl_preserves_precedence_inheritance_and_freshness(library):
    tid, owner_row = seed(library)
    hidden = library.post(
        f"/api/v1/tables/{tid}/rows",
        headers=auth(),
        json={"values": {"title": "secret phrase", "confidence": 0.1}},
    ).json()["data"]["_row_id"]

    # A broad path grant is overridden by a table deny. A direct row request can
    # still be granted by a row ACL because row scope is the most specific rule.
    assert put_acl(library, "path", "/数学", {"*": ["read"]}).status_code == 200
    assert put_acl(library, "table", tid, {"bob": []}).status_code == 200
    assert put_acl(library, "row", f"{tid}:{owner_row}", {"bob": ["read"]}).status_code == 200
    assert library.get(f"/api/v1/tables/{tid}/rows/{owner_row}", headers=auth("bob")).status_code == 200

    rows = library.get(f"/api/v1/tables/{tid}/rows?limit=100", headers=auth("bob"))
    query = library.post(
        "/api/v1/query", headers=auth("bob"), json={"expression": 'FROM "/数学/**" LIMIT 100'}
    )
    text = library.post(
        "/api/v1/search/text", headers=auth("bob"), json={"query": "一致收敛", "limit": 10}
    )
    assert rows.status_code == 403
    assert query.status_code == text.status_code == 200
    assert query.json()["data"] == []
    assert text.json()["data"] == []
    hidden_text = library.post(
        "/api/v1/search/text", headers=auth("bob"), json={"query": "secret", "limit": 10}
    )
    assert hidden_text.json()["data"] == []

    # With a table grant, an explicit row deny stops fallback to that grant and
    # prevents row data or snippets from leaking through list/query/text routes.
    assert put_acl(library, "table", tid, {"bob": ["read"]}).status_code == 200
    assert put_acl(library, "row", f"{tid}:{owner_row}", {"bob": []}).status_code == 200
    assert library.get(f"/api/v1/tables/{tid}/rows/{owner_row}", headers=auth("bob")).status_code == 403
    rows = library.get(f"/api/v1/tables/{tid}/rows?limit=100", headers=auth("bob"))
    query = library.post(
        "/api/v1/query", headers=auth("bob"), json={"expression": 'FROM "/数学/**" LIMIT 100'}
    )
    text = library.post(
        "/api/v1/search/text", headers=auth("bob"), json={"query": "一致收敛", "limit": 10}
    )
    assert [row["_row_id"] for row in rows.json()["data"]] == [hidden]
    assert [row["_row_id"] for row in query.json()["data"]] == [hidden]
    assert text.json()["data"] == []

    # Changing the row ACL is visible to the next request. Clearing both row and
    # table overrides restores inherited wildcard access immediately.
    assert put_acl(library, "row", f"{tid}:{owner_row}", {"bob": ["read"]}).status_code == 200
    assert len(library.get(f"/api/v1/tables/{tid}/rows?limit=100", headers=auth("bob")).json()["data"]) == 2
    for resource_type, resource_id in (("row", f"{tid}:{owner_row}"), ("table", tid)):
        current = library.get(f"/api/v1/acl/{resource_type}/{resource_id}", headers=auth("admin"))
        assert current.status_code == 200
        assert library.delete(
            f"/api/v1/acl/{resource_type}/{resource_id}",
            headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
        ).status_code == 200
    inherited = library.get(f"/api/v1/tables/{tid}/rows?limit=100", headers=auth("bob"))
    assert {row["_row_id"] for row in inherited.json()["data"]} == {owner_row, hidden}

    assert put_acl(library, "path", "/数学/分析", {"bob": []}).status_code == 200
    denied = library.post(
        "/api/v1/query", headers=auth("bob"), json={"expression": 'FROM "/数学/**" LIMIT 100'}
    )
    assert denied.json()["data"] == []
    nearest = library.get("/api/v1/acl/path/" + quote("/数学/分析", safe=""), headers=auth("admin"))
    assert nearest.status_code == 200
    assert library.delete(
        "/api/v1/acl/path/" + quote("/数学/分析", safe=""),
        headers=auth("admin", **{"If-Match": nearest.headers["ETag"]}),
    ).status_code == 200

    # The current owner is still inherited from the verified account tree when
    # there is no explicit policy anywhere on the resource chain.
    parent = library.get("/api/v1/acl/path/" + quote("/数学", safe=""), headers=auth("admin"))
    assert library.delete(
        "/api/v1/acl/path/" + quote("/数学", safe=""),
        headers=auth("admin", **{"If-Match": parent.headers["ETag"]}),
    ).status_code == 200
    library.app.state.settings.dev_tokens["tree-child"] = {
        "sub": "child",
        "scope": "xelibrary:read xelibrary:query",
        "agent_kind": "human",
        "ancestors": ["alice"],
    }
    child_auth = auth("tree-child")
    owner_rows = library.get(f"/api/v1/tables/{tid}/rows?limit=100", headers=child_auth)
    assert owner_rows.status_code == 200, owner_rows.text
    assert len(owner_rows.json()["data"]) == 2
    assert len(
        library.post(
            "/api/v1/query", headers=child_auth, json={"expression": 'FROM "/数学/**" LIMIT 100'}
        ).json()["data"]
    ) == 2
    assert len(
        library.post(
            "/api/v1/search/text", headers=child_auth, json={"query": "secret", "limit": 10}
        ).json()["data"]
    ) == 1


def test_cjk_text_search_merges_new_index_and_bounded_legacy_scan(library, settings, monkeypatch):
    tid, _ = seed(library)
    # A normally written row is indexed in the additive segmented CJK index.
    indexed = library.post(
        f"/api/v1/tables/{tid}/rows",
        headers=auth(),
        json={"values": {"title": "积分换元", "confidence": 0.8}},
    )
    assert indexed.status_code == 201, indexed.text
    indexed_id = indexed.json()["data"]["_row_id"]

    # Emulate a row from a pre-CJK-index .xl file: the legacy FTS body is raw
    # CJK text and there is deliberately no corresponding __xl_fts_cjk row.
    legacy_id = str(uuid4())
    table_file = settings.data_dir / "tables" / f"{tid}.xl"
    stamp = "2026-10-01T00:00:00Z"
    with sqlite3.connect(table_file) as db:
        db.execute(
            "INSERT INTO data VALUES(?,?,?,?,?,?)",
            (legacy_id, 1, stamp, stamp, None, json.dumps({"title": "旧式前缀积分交换", "confidence": 0.7})),
        )
        db.execute("INSERT INTO __xl_fts VALUES(?,?)", (legacy_id, "旧式前缀积分交换 0.7"))

    response = library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"})
    assert response.status_code == 200, response.text
    hits = {hit["row_id"]: hit for hit in response.json()["data"]}
    assert set(hits) == {indexed_id, legacy_id}
    assert hits[indexed_id]["score_type"] == "sqlite_fts_cjk"
    assert hits[indexed_id]["approximate"] is False
    assert hits[legacy_id]["score_type"] == "substring_scan"
    assert hits[legacy_id]["approximate"] is True
    assert response.json()["meta"]["partial"] is False

    # The scan budget is still applied with an indexed hit already present.
    monkeypatch.setenv("XELIBRARY_CJK_SCAN_ROWS", "1")
    capped = library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"})
    assert capped.status_code == 200, capped.text
    assert capped.json()["meta"]["partial"] is True
    assert any(hit["row_id"] == indexed_id for hit in capped.json()["data"])
    monkeypatch.setenv("XELIBRARY_CJK_SCAN_ROWS", "3")
    exact = library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"})
    assert exact.json()["meta"]["partial"] is False
    other_tid = create_empty_table(library, "CJK scan boundary")
    other_row = library.post(
        f"/api/v1/tables/{other_tid}/rows", headers=auth(), json={"values": {"body": "无关行"}}
    )
    assert other_row.status_code == 201, other_row.text
    across_tables = library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"})
    assert across_tables.json()["meta"]["partial"] is True

    # Literal FTS escaping, filters and path scope remain enforced.
    literal = library.post(
        "/api/v1/search/text", headers=auth(), json={"query": '积分 OR "*', "limit": 10}
    )
    assert literal.status_code == 200, literal.text
    assert literal.json()["data"] == []
    filtered = library.post(
        "/api/v1/search/text",
        headers=auth(),
        json={"query": "积分", "filters": {"confidence": 0.8}},
    )
    assert {hit["row_id"] for hit in filtered.json()["data"]} == {indexed_id}
    outside = library.post(
        "/api/v1/search/text", headers=auth(), json={"query": "积分", "path": "/does-not-match/**"}
    )
    assert outside.json()["data"] == []

    english = library.post(
        f"/api/v1/tables/{tid}/rows",
        headers=auth(),
        json={"values": {"title": "plain english phrase", "confidence": 0.5}},
    )
    english_hit = library.post(
        "/api/v1/search/text", headers=auth(), json={"query": "phrase"}
    ).json()["data"]
    assert [hit["row_id"] for hit in english_hit] == [english.json()["data"]["_row_id"]]
    assert english_hit[0]["score_type"] == "sqlite_fts_bm25"
    assert english_hit[0]["approximate"] is False


def test_cjk_index_tracks_patch_delete_restore_and_schema_rebuild(library):
    tid, rid = seed(library)
    row_url = f"/api/v1/tables/{tid}/rows/{rid}"
    before = library.get(row_url, headers=auth())
    assert before.status_code == 200
    assert library.post("/api/v1/search/text", headers=auth(), json={"query": "一致收敛"}).json()["data"]

    changed = library.patch(
        row_url, headers=auth(**{"If-Match": before.headers["ETag"]}), json={"values": {"title": "积分交换"}}
    )
    assert changed.status_code == 200, changed.text
    assert not library.post(
        "/api/v1/search/text", headers=auth(), json={"query": "一致收敛"}
    ).json()["data"]
    assert library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"}).json()["data"]

    deleted = library.delete(row_url, headers=auth(**{"If-Match": changed.headers["ETag"]}))
    assert deleted.status_code == 200, deleted.text
    assert not library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"}).json()["data"]
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        assert db.execute("SELECT 1 FROM __xl_fts_cjk WHERE row_id=?", (rid,)).fetchone() is None
    restored = library.post(
        row_url + "/restore",
        headers=auth(**{"If-Match": '"revision:3"'}),
        json={"revision": 2},
    )
    assert restored.status_code == 200, restored.text
    assert library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"}).json()["data"]

    # Simulate an existing .xl created before this feature; schema migration
    # installs and rebuilds the additive index in the same transaction.
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("DROP TABLE __xl_fts_cjk")
    schema = library.get(f"/api/v1/tables/{tid}/schema", headers=auth("admin"))
    rebuilt = library.patch(
        f"/api/v1/tables/{tid}/schema",
        headers=auth("admin", **{"If-Match": schema.headers["ETag"]}),
        json={"columns": [
            {"name": "title", "type": "string", "nullable": False},
            {"name": "confidence", "type": "float64"},
            {"name": "note", "type": "text", "nullable": True},
        ]},
    )
    assert rebuilt.status_code == 200, rebuilt.text
    assert library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"}).json()["data"]

    # The raw legacy index remains readable by the pre-change literal search
    # strategy after mutations/rebuild, so code rollback needs no data rollback.
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        legacy_body = db.execute("SELECT body FROM __xl_fts WHERE row_id=?", (rid,)).fetchone()[0]
        cjk_body = db.execute("SELECT body FROM __xl_fts_cjk WHERE row_id=?", (rid,)).fetchone()[0]
        legacy_hit = db.execute(
            "SELECT 1 FROM __xl_fts WHERE __xl_fts MATCH ? AND row_id=?",
            ('"积分交换"', rid),
        ).fetchone()
    assert legacy_body == "积分交换 0.92 None"
    assert "积 分 交 换" in cjk_body
    assert legacy_hit is not None  # an old binary can still search the unchanged legacy index

    # Simulate an old binary changing only the source row and legacy index. The
    # CJK table is stale, but current-source validation prevents a stale match.
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        values = {"title": "回滚后二进制写入", "confidence": 0.92, "note": None}
        db.execute("UPDATE data SET values_json=?,_revision=_revision+1 WHERE _row_id=?", (json.dumps(values), rid))
        db.execute("DELETE FROM __xl_fts WHERE row_id=?", (rid,))
        db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, "回滚后二进制写入 0.92 None"))
    stale = library.post("/api/v1/search/text", headers=auth(), json={"query": "积分"})
    fresh = library.post("/api/v1/search/text", headers=auth(), json={"query": "回滚"})
    assert rid not in {hit["row_id"] for hit in stale.json()["data"]}
    assert rid in {hit["row_id"] for hit in fresh.json()["data"]}

    current = library.get(row_url, headers=auth("admin"))
    purged = library.delete(
        row_url + "?purge=true",
        headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
    )
    assert purged.status_code == 200, purged.text
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        assert db.execute("SELECT 1 FROM __xl_fts_cjk WHERE row_id=?", (rid,)).fetchone() is None


def test_cjk_search_scans_an_older_table_with_no_additive_index(library, settings):
    tid, rid = seed(library)
    with sqlite3.connect(settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("DROP TABLE __xl_fts_cjk")
    response = library.post("/api/v1/search/text", headers=auth(), json={"query": "一致收"})
    assert response.status_code == 200, response.text
    assert [hit["row_id"] for hit in response.json()["data"]] == [rid]
    hit = response.json()["data"][0]
    assert hit["score_type"] == "substring_scan"
    assert hit["approximate"] is True


def test_cjk_indexed_mixed_language_hit_bypasses_single_table_scan_budget(library, monkeypatch):
    tid = create_empty_table(library, "CJK mixed query in one table")
    url = f"/api/v1/tables/{tid}/rows"
    created = [
        library.post(url, headers=auth(), json={"values": {"body": "placeholder"}})
        for _ in range(2)
    ]
    assert all(result.status_code == 201 for result in created)
    earlier, target = sorted(created, key=lambda result: result.json()["data"]["_row_id"])
    unrelated_patch = library.patch(
        f"{url}/{earlier.json()['data']['_row_id']}",
        headers=auth(**{"If-Match": earlier.headers["ETag"]}),
        json={"values": {"body": "unrelated row"}},
    )
    target_patch = library.patch(
        f"{url}/{target.json()['data']['_row_id']}",
        headers=auth(**{"If-Match": target.headers["ETag"]}),
        json={"values": {"body": "前缀积分后缀 convergence"}},
    )
    assert unrelated_patch.status_code == target_patch.status_code == 200

    monkeypatch.setenv("XELIBRARY_CJK_SCAN_ROWS", "1")
    result = library.post(
        "/api/v1/search/text", headers=auth(), json={"query": "积分 convergence"}
    )
    assert result.status_code == 200, result.text
    assert result.json()["meta"]["partial"] is True
    assert [hit["row_id"] for hit in result.json()["data"]] == [target.json()["data"]["_row_id"]]
    assert result.json()["data"][0]["score_type"] == "sqlite_fts_cjk"


def test_cjk_indexed_hit_in_later_table_is_independent_of_scan_budget(library, monkeypatch):
    first_tid = create_empty_table(library, "CJK lower id table")
    second_tid = create_empty_table(library, "CJK higher id table")
    unrelated_tid, target_tid = sorted((first_tid, second_tid))
    unrelated = library.post(
        f"/api/v1/tables/{unrelated_tid}/rows",
        headers=auth(),
        json={"values": {"body": "unrelated row"}},
    )
    target = library.post(
        f"/api/v1/tables/{target_tid}/rows",
        headers=auth(),
        json={"values": {"body": "前缀积分后缀 convergence"}},
    )
    assert unrelated.status_code == target.status_code == 201

    monkeypatch.setenv("XELIBRARY_CJK_SCAN_ROWS", "1")
    result = library.post(
        "/api/v1/search/text", headers=auth(), json={"query": "积分 convergence"}
    )
    assert result.status_code == 200, result.text
    assert result.json()["meta"]["partial"] is True
    assert [hit["row_id"] for hit in result.json()["data"]] == [target.json()["data"]["_row_id"]]
    assert result.json()["data"][0]["table_id"] == target_tid
    assert result.json()["data"][0]["score_type"] == "sqlite_fts_cjk"


def test_cjk_index_mutation_failure_rolls_back_row_and_both_indexes(library, settings, monkeypatch):
    tid, rid = seed(library)
    row_url = f"/api/v1/tables/{tid}/rows/{rid}"
    before = library.get(row_url, headers=auth())
    import xeonolab.library as library_module

    def fail_index(*_args, **_kwargs):
        raise RuntimeError("injected CJK index failure")

    monkeypatch.setattr(library_module, "index_cjk_row", fail_index)
    with pytest.raises(RuntimeError, match="injected CJK index failure"):
        library.patch(
            row_url,
            headers=auth(**{"If-Match": before.headers["ETag"]}),
            json={"values": {"title": "积分交换"}},
        )

    current = library.get(row_url, headers=auth())
    assert current.status_code == 200
    assert current.json()["data"]["values"]["title"] == "一致收敛"
    with sqlite3.connect(settings.data_dir / "tables" / f"{tid}.xl") as db:
        legacy = db.execute("SELECT body FROM __xl_fts WHERE row_id=?", (rid,)).fetchone()[0]
        cjk = db.execute("SELECT body FROM __xl_fts_cjk WHERE row_id=?", (rid,)).fetchone()[0]
    assert legacy == "一致收敛 0.92"
    assert "一 致 收 敛" in cjk


def test_row_acl_catalogue_connections_are_bounded_per_request(library, settings, monkeypatch):
    tid, _ = seed(library)
    table_file = settings.data_dir / "tables" / f"{tid}.xl"
    stamp = "2026-10-01T00:00:00Z"
    with sqlite3.connect(table_file) as db:
        for index in range(120):
            rid = str(uuid4())
            values = {"title": f"bulk benchmark matching {index}", "confidence": 0.5}
            db.execute(
                "INSERT INTO data VALUES(?,?,?,?,?,?)",
                (rid, 1, stamp, stamp, None, json.dumps(values)),
            )
            db.execute("INSERT INTO __xl_fts VALUES(?,?)", (rid, " ".join(map(str, values.values()))))

    import xeonolab.library as library_module

    catalogue = settings.data_dir / "catalogue.sqlite"
    original_connect = library_module.connect

    class CountedConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, *args, **kwargs):
            return self.connection.execute(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    opened = []

    def counting_connect(path):
        connection = original_connect(path)
        if str(path) == str(catalogue):
            opened.append(str(path))
            return CountedConnection(connection)
        return connection

    monkeypatch.setattr(library_module, "connect", counting_connect)

    responses = (
        library.get(f"/api/v1/tables/{tid}/rows?limit=10", headers=auth()),
        library.post("/api/v1/query", headers=auth(), json={"expression": 'FROM "/数学/**" LIMIT 10'}),
        library.post("/api/v1/search/text", headers=auth(), json={"query": "matching", "limit": 10}),
    )
    for result in responses:
        assert result.status_code == 200, result.text
        assert result.json()["data"]
    assert len(opened) == 3 * len(responses)


def test_row_acl_snapshot_uses_composite_index_with_large_unrelated_catalogue(
    library, settings, monkeypatch
):
    tid, rid = seed(library)
    catalogue = settings.data_dir / "catalogue.sqlite"
    external_table = str(uuid4())
    unrelated = [
        (
            "row",
            f"{external_table}:{uuid4()}",
            "{}",
            1,
            1,
        )
        for _ in range(12_000)
    ]
    with sqlite3.connect(catalogue) as db:
        db.executemany(
            "INSERT INTO acl(resource_type,resource_id,grants,revision,explicit) VALUES(?,?,?,?,?)",
            unrelated,
        )
        db.executemany(
            "INSERT INTO acl(resource_type,resource_id,grants,revision,explicit) VALUES(?,?,?,?,?)",
            [
                ("table", tid, json.dumps({"bob": ["read"]}), 1, 1),
                ("row", f"{tid}:{rid}", json.dumps({"bob": []}), 1, 1),
            ],
        )
    import xeonolab.library as library_module

    original_connect = library_module.connect
    plans = []

    class ExplainConnection:
        def __init__(self, connection):
            self.connection = connection

        def __enter__(self):
            self.connection.__enter__()
            return self

        def __exit__(self, *args):
            return self.connection.__exit__(*args)

        def execute(self, sql, parameters=()):
            if sql.startswith("SELECT resource_type,resource_id,grants,explicit FROM acl WHERE"):
                plans.append(
                    [
                        row[3]
                        for row in self.connection.execute("EXPLAIN QUERY PLAN " + sql, parameters)
                    ]
                )
            return self.connection.execute(sql, parameters)

        def __getattr__(self, name):
            return getattr(self.connection, name)

    def explain_connect(path):
        connection = original_connect(path)
        return ExplainConnection(connection) if str(path) == str(catalogue) else connection

    monkeypatch.setattr(library_module, "connect", explain_connect)
    rows = library.get(f"/api/v1/tables/{tid}/rows", headers=auth("bob"))
    assert rows.status_code == 200, rows.text
    assert rows.json()["data"] == []
    assert len(plans) == 1
    plan = plans[0]
    row_branch = next(line for line in plan if "resource_type=? AND resource_id>? AND resource_id<?" in line)
    assert "sqlite_autoindex_acl_1" in row_branch
    assert not any(line.startswith("SCAN acl") for line in plan)


def test_acl_revision_etag_concurrency_and_inheritance_clear(library):
    tid, _ = seed(library)
    path = f"/api/v1/acl/table/{tid}"
    initial = library.get(path, headers=auth("admin"))
    assert initial.status_code == 200
    assert initial.headers["ETag"] == '"revision:0"'
    assert initial.json()["data"]["revision"] == 0

    body = {"grants": {"bob": ["read"]}}
    assert library.put(path, headers=auth("admin"), json=body).status_code == 412

    def update(_):
        return library.put(path, headers=auth("admin", **{"If-Match": initial.headers["ETag"]}), json=body)

    with ThreadPoolExecutor(2) as pool:
        responses = list(pool.map(update, [1, 2]))
    assert sorted(response.status_code for response in responses) == [200, 412]
    current = library.get(path, headers=auth("admin"))
    assert current.headers["ETag"] == '"revision:1"'
    assert current.json()["data"]["explicit"] is True
    assert current.json()["data"]["grants"] == {"bob": ["read"]}
    assert put_acl(library, "path", "/数学", {"bob": ["read"]}).status_code == 200

    cleared = library.delete(path, headers=auth("admin", **{"If-Match": current.headers["ETag"]}))
    assert cleared.status_code == 200
    assert cleared.headers["ETag"] == '"revision:2"'
    assert cleared.json()["data"]["explicit"] is False
    assert cleared.json()["data"]["grants"] == {}
    # Removing the table override restores the matching ancestor path ACL.
    assert library.get(f"/api/v1/tables/{tid}", headers=auth("bob")).status_code == 200
    assert library.delete(path, headers=auth("admin", **{"If-Match": current.headers["ETag"]})).status_code == 412


def test_acl_put_and_clear_compete_on_one_revision(library):
    tid, _ = seed(library)
    path = f"/api/v1/acl/table/{tid}"
    initial = library.get(path, headers=auth("admin"))
    assert initial.status_code == 200
    headers = {"If-Match": initial.headers["ETag"]}

    def set_explicit(_):
        return library.put(path, headers=auth("admin", **headers), json={"grants": {"bob": ["read"]}})

    def restore_inheritance(_):
        return library.delete(path, headers=auth("admin", **headers))

    with ThreadPoolExecutor(2) as pool:
        writes = list(pool.map(lambda action: action(0), [set_explicit, restore_inheritance]))

    assert sorted(result.status_code for result in writes) == [200, 412]
    current = library.get(path, headers=auth("admin"))
    assert current.headers["ETag"] == '"revision:1"'
    if current.json()["data"]["explicit"]:
        assert current.json()["data"]["grants"] == {"bob": ["read"]}
    else:
        assert current.json()["data"]["grants"] == {}


def test_acl_listing_is_cursor_paginated_and_admin_only(library):
    tid, rid = seed(library)
    assert put_acl(library, "table", tid, {"bob": ["read"]}).status_code == 200
    assert put_acl(library, "row", f"{tid}:{rid}", {"bob": ["read"]}).status_code == 200
    assert library.get("/api/v1/acl", headers=auth()).status_code == 403
    first = library.get("/api/v1/acl?limit=1", headers=auth("admin"))
    assert first.status_code == 200
    assert len(first.json()["data"]) == 1
    cursor = first.json()["meta"]["next_cursor"]
    assert cursor
    assert first.json()["data"][0]["explicit"] is True
    assert isinstance(first.json()["data"][0]["grants"], dict)
    second = library.get("/api/v1/acl", params={"limit": 1, "cursor": cursor}, headers=auth("admin"))
    assert second.status_code == 200
    assert len(second.json()["data"]) == 1
    assert second.json()["data"][0]["resource_id"] != first.json()["data"][0]["resource_id"]
    path = "/api/v1/acl/path/" + quote("/数学/分析", safe="")
    assert library.get(path, headers=auth("admin")).status_code == 200


def test_acl_old_catalog_migrates_revision_and_explicit_without_data_loss(settings):
    import sqlite3

    from fastapi.testclient import TestClient

    from xeonolab.library import create_app

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    catalogue = settings.data_dir / "catalogue.sqlite"
    with sqlite3.connect(catalogue) as db:
        db.executescript("""
            CREATE TABLE tables(id TEXT PRIMARY KEY,path TEXT,name TEXT,owner TEXT,UNIQUE(path,name));
            CREATE TABLE acl(resource_type TEXT,resource_id TEXT,grants TEXT,PRIMARY KEY(resource_type,resource_id));
            CREATE TABLE audit(id TEXT PRIMARY KEY,actor TEXT,event TEXT,resource TEXT,at TEXT);
        """)
        db.execute("INSERT INTO acl VALUES(?,?,?)", ("path", "/legacy", '{"bob":["read"]}'))

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/acl/path//legacy", headers=auth("admin"))
    assert response.status_code == 200
    assert response.headers["ETag"] == '"revision:1"'
    assert response.json()["data"]["explicit"] is True
    assert response.json()["data"]["grants"] == {"bob": ["read"]}


def test_job_queue_view_is_admin_only_and_filterable(library, settings):
    from xeonolab.jobs import JobStore

    jobs = JobStore(settings.data_dir / "catalogue.sqlite")
    jobs.create("job-1", "admin", "table-a")
    jobs.update("job-1", status="completed")
    jobs.create("job-3", "admin", "table-b")
    jobs.create("job-2", "bob", "table-b")
    assert library.get("/api/v1/jobs", headers=auth()).status_code == 403
    result = library.get("/api/v1/jobs", headers=auth("admin"))
    assert result.status_code == 200
    body = result.json()
    assert [job["job_id"] for job in body["data"]] == ["job-3", "job-1"]
    assert body["meta"]["counts"] == {"queued": 1, "running": 0, "completed": 1, "failed": 0}
    completed = library.get("/api/v1/jobs?status=completed", headers=auth("admin")).json()
    assert [job["job_id"] for job in completed["data"]] == ["job-1"]
    scoped = library.get("/api/v1/jobs?table_id=table-a", headers=auth("admin")).json()
    assert [job["job_id"] for job in scoped["data"]] == ["job-1"]
    assert scoped["meta"]["counts"]["completed"] == 1 and scoped["meta"]["counts"]["queued"] == 0
    assert library.get("/api/v1/jobs/job-2", headers=auth("admin")).status_code == 403
    assert library.get("/api/v1/jobs?limit=0", headers=auth("admin")).status_code == 422
    assert library.get("/api/v1/jobs?limit=201", headers=auth("admin")).status_code == 422
    assert library.get("/api/v1/jobs?status=unknown", headers=auth("admin")).status_code == 422


def test_embedding_index_queue_read_is_embedding_scope_and_strictly_owner_only(library, settings):
    from xeonolab.jobs import JobStore

    settings.dev_tokens["alice"]["scope"] += " embedding:read"
    settings.dev_tokens["bob"]["scope"] += " embedding:read"
    jobs = JobStore(settings.data_dir / "catalogue.sqlite")
    jobs.create("alice-queued", "alice", "table-a")
    jobs.create("alice-done", "alice", "table-b")
    jobs.update("alice-done", status="completed", error="")
    jobs.create("bob-only", "bob", "table-a")
    jobs.create("admin-only", "admin", "table-a")

    own = library.get("/api/v1/embedding/jobs", headers=auth("alice"))
    assert own.status_code == 200, own.text
    body = own.json()
    assert {job["job_id"] for job in body["data"]} == {"alice-queued", "alice-done"}
    assert all("owner" not in job for job in body["data"])
    assert body["meta"]["counts"] == {"queued": 1, "running": 0, "completed": 1, "failed": 0}
    filtered = library.get("/api/v1/embedding/jobs?status=completed", headers=auth("alice"))
    assert [job["job_id"] for job in filtered.json()["data"]] == ["alice-done"]
    assert library.get("/api/v1/embedding/jobs", headers=auth("opencode")).status_code == 403
    assert library.get("/api/v1/embedding/jobs?limit=0", headers=auth("alice")).status_code == 422
    assert library.get("/api/v1/jobs", headers=auth("alice")).status_code == 403
