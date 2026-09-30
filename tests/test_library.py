from concurrent.futures import ThreadPoolExecutor

from .conftest import auth


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
        library.put(
            f"/api/v1/acl/table/{tid}",
            headers=auth("admin"),
            json={
                "grants": {
                    "alice": ["read", "write", "attach"],
                    "bob": ["read"],
                    "attach-only": ["attach"],
                    "read-attach": ["read", "attach"],
                }
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
    assert library.put(
        f"/api/v1/acl/row/{tid}:{rid}",
        headers=auth("admin"),
        json={"grants": {"alice": ["read", "attach"], "bob": ["read"], "read-attach": ["attach"]}},
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
    assert library.put(
        f"/api/v1/acl/row/{tid}:{rid2}",
        headers=auth("admin"),
        json={"grants": {"alice": ["read", "attach"]}},
    ).status_code == 200
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
    library.put("/api/v1/acl/path//数学", headers=auth("admin"), json={"grants": {"bob": ["read"]}})
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
    assert library.put(
        f"/api/v1/acl/table/{tid}", headers=auth("admin"), json={"grants": {"bob": ["read"]}}
    ).status_code == 200
    assert library.put(
        f"/api/v1/acl/row/{tid}:{rid}", headers=auth("admin"), json={"grants": {}}
    ).status_code == 200
    assert library.get(rows_path + "?include_deleted=true", headers=auth("bob")).json()["data"] == []

    assert library.put(
        f"/api/v1/acl/row/{tid}:{rid}",
        headers=auth("admin"),
        json={"grants": {"bob": ["read"]}},
    ).status_code == 200
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
    assert library.put(path, headers=auth("admin"), json={"grants": {"bob": ["read"]}}).status_code == 200
    assert library.get(path, headers=auth("admin")).json()["data"]["grants"] == {"bob": ["read"]}
    assert any(
        r["event"] == "acl.changed"
        for r in library.get("/api/v1/audit", headers=auth("admin")).json()["data"]
    )
