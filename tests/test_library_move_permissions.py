"""Table move destination checks: table write plus the destination path policy (Issue #6 review)."""

from .conftest import auth, put_acl


def create_table(library, path="/源", name="移动权限"):
    created = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": path, "name": name, "columns": [{"name": "title", "type": "string"}]},
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["table_id"]


def etag(library, table_id, token):
    response = library.get(f"/api/v1/tables/{table_id}/schema", headers=auth(token))
    assert response.status_code == 200, response.text
    return response.headers["ETag"]


def move(library, table_id, path, token):
    return library.post(
        f"/api/v1/tables/{table_id}/move",
        headers=auth(token, **{"If-Match": etag(library, table_id, token)}),
        json={"path": path},
    )


def grant_table_write(library, table_id):
    response = put_acl(
        library,
        "table",
        table_id,
        {"alice": ["read", "write", "attach"], "bob": ["read", "write"]},
    )
    assert response.status_code == 200, response.text


def test_explicit_table_write_permits_move_into_unrestricted_path(library):
    table_id = create_table(library)
    grant_table_write(library, table_id)
    moved = move(library, table_id, "/目标", "bob")
    assert moved.status_code == 200, moved.text
    assert moved.json()["data"]["path"] == "/目标"
    detail = library.get(f"/api/v1/tables/{table_id}", headers=auth("bob"))
    assert detail.json()["data"]["permissions"]["write"] is True


def test_destination_path_acl_read_only_blocks_move(library):
    table_id = create_table(library, name="目标只读")
    grant_table_write(library, table_id)
    assert put_acl(library, "path", "/禁区", {"bob": ["read"]}).status_code == 200
    blocked = move(library, table_id, "/禁区", "bob")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "XL_FORBIDDEN"


def test_nearer_destination_path_acl_is_enforced(library):
    table_id = create_table(library, name="父路径只读")
    grant_table_write(library, table_id)
    assert put_acl(library, "path", "/父", {"bob": ["read"]}).status_code == 200
    blocked = move(library, table_id, "/父/子", "bob")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "XL_FORBIDDEN"


def test_destination_path_acl_write_permits_move(library):
    table_id = create_table(library, name="目标可写")
    grant_table_write(library, table_id)
    assert put_acl(library, "path", "/开放", {"bob": ["write"]}).status_code == 200
    moved = move(library, table_id, "/开放", "bob")
    assert moved.status_code == 200, moved.text


def test_owner_destination_explicit_deny_blocks_move(library):
    table_id = create_table(library, name="owner拒绝")
    assert put_acl(library, "path", "/拒绝", {"alice": []}).status_code == 200
    blocked = move(library, table_id, "/拒绝", "alice")
    assert blocked.status_code == 403
    assert blocked.json()["error"]["code"] == "XL_FORBIDDEN"
