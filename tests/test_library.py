import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote
from uuid import uuid4

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
