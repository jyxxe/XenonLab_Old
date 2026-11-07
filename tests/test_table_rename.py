"""Table rename keeps paths/ACLs intact while bumping the revision (issue #6)."""

from __future__ import annotations

from .conftest import auth


def create_table(client, path: str, name: str):
    result = client.post(
        "/api/v1/tables",
        headers=auth("alice"),
        json={"path": path, "name": name, "columns": [{"name": "body", "type": "text", "nullable": True}]},
    )
    assert result.status_code == 201, result.text
    return result.json()["data"]["table_id"]


def table_etag(client, tid: str) -> str:
    result = client.get(f"/api/v1/tables/{tid}/schema", headers=auth("alice"))
    assert result.status_code == 200, result.text
    return result.headers["ETag"]


def test_rename_updates_name_and_bumps_revision(library):
    tid = create_table(library, "/数学/分析", "旧名字")
    etag = table_etag(library, tid)
    result = library.patch(
        f"/api/v1/tables/{tid}", headers=auth("alice", **{"If-Match": etag}), json={"name": "新名字"}
    )
    assert result.status_code == 200, result.text
    data = result.json()["data"]
    assert data["name"] == "新名字" and data["path"] == "/数学/分析"
    assert data["table_revision"] > 1
    assert result.headers["ETag"] != etag
    detail = library.get(f"/api/v1/tables/{tid}/schema", headers=auth("alice")).json()["data"]
    assert detail["name"] == "新名字"
    # Path and ACL resources are unchanged: the same path ACL still governs access.
    assert detail["permissions"]


def test_rename_requires_if_match_and_rejects_duplicates(library):
    first = create_table(library, "/数学/分析", "A")
    create_table(library, "/数学/分析", "B")
    missing = library.patch(f"/api/v1/tables/{first}", headers=auth("alice"), json={"name": "C"})
    assert missing.status_code == 412, missing.text
    etag = table_etag(library, first)
    conflict = library.patch(
        f"/api/v1/tables/{first}", headers=auth("alice", **{"If-Match": etag}), json={"name": "B"}
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "TABLE_EXISTS"


def test_rename_validation_and_permissions(library):
    tid = create_table(library, "/数学/分析", "A")
    etag = table_etag(library, tid)
    empty = library.patch(
        f"/api/v1/tables/{tid}", headers=auth("alice", **{"If-Match": etag}), json={"name": ""}
    )
    assert empty.status_code == 422
    forbidden = library.patch(
        f"/api/v1/tables/{tid}", headers=auth("bob", **{"If-Match": etag}), json={"name": "Hijack"}
    )
    assert forbidden.status_code == 403
    unknown = library.patch(
        "/api/v1/tables/00000000-0000-0000-0000-000000000000",
        headers=auth("alice", **{"If-Match": etag}),
        json={"name": "Nope"},
    )
    assert unknown.status_code == 404
    assert library.patch(f"/api/v1/tables/{tid}", headers=auth("alice"), json={"name": "X"}).status_code == 412
