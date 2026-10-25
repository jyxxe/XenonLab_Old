"""Table-level permissions returned to the caller (Issue #48 UI gating)."""

from .conftest import auth, put_acl


def create_table(library, name="权限表"):
    created = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/权限", "name": name, "columns": [{"name": "title", "type": "string"}]},
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["table_id"]


def test_owner_and_admin_see_full_table_permissions(library):
    table_id = create_table(library)
    detail = library.get(f"/api/v1/tables/{table_id}", headers=auth())
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["permissions"] == {"read": True, "write": True, "attach": True}

    admin = library.get(f"/api/v1/tables/{table_id}", headers=auth("admin"))
    assert admin.status_code == 200, admin.text
    assert admin.json()["data"]["permissions"] == {"read": True, "write": True, "attach": True}


def test_explicit_table_acl_limits_write_and_attach_permissions(library):
    table_id = create_table(library)
    read_only = put_acl(library, "table", table_id, {"bob": ["read"]})
    assert read_only.status_code == 200, read_only.text

    detail = library.get(f"/api/v1/tables/{table_id}", headers=auth("bob"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["data"]["permissions"] == {"read": True, "write": False, "attach": False}

    granted = put_acl(library, "table", table_id, {"bob": ["read", "write", "attach"]})
    assert granted.status_code == 200, granted.text
    detail = library.get(f"/api/v1/tables/{table_id}", headers=auth("bob"))
    assert detail.json()["data"]["permissions"] == {"read": True, "write": True, "attach": True}


def test_explicit_deny_keeps_owner_write_when_other_principals_are_granted(library):
    table_id = create_table(library)
    denied = put_acl(library, "table", table_id, {"bob": ["read"], "alice": []})
    assert denied.status_code == 200, denied.text

    owner = library.get(f"/api/v1/tables/{table_id}", headers=auth())
    assert owner.status_code == 403
    assert owner.json()["error"]["code"] == "XL_FORBIDDEN"


def test_list_tables_includes_caller_permissions(library):
    table_id = create_table(library)
    listing = library.get("/api/v1/tables", headers=auth())
    assert listing.status_code == 200, listing.text
    row = next(item for item in listing.json()["data"] if item["id"] == table_id)
    assert row["permissions"] == {"read": True, "write": True, "attach": True}

    put_acl(library, "table", table_id, {"bob": ["read"]})
    bob_listing = library.get("/api/v1/tables", headers=auth("bob"))
    assert bob_listing.status_code == 200, bob_listing.text
    bob_row = next(item for item in bob_listing.json()["data"] if item["id"] == table_id)
    assert bob_row["permissions"] == {"read": True, "write": False, "attach": False}
