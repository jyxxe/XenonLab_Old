"""Metadata discovery contracts; no content object is created by these fixtures."""
import base64
import json
import sqlite3
from uuid import UUID

import pytest

from .conftest import auth, put_acl
from .test_library import seed


def populate(client, tid, rid, count=3, start=1, filename="proof.pdf"):
    with sqlite3.connect(client.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.executemany(
            "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
            [(str(UUID(int=i)), rid, "synthetic-not-an-object", filename, "application/pdf", 1234,
              "2026-01-01T00:00:00Z", "{}") for i in range(start, start + count)],
        )
    return [str(UUID(int=i)) for i in range(start, start + count)]


def page(client, tid, token="alice", **params):
    return client.get(f"/api/v1/tables/{tid}/attachments", headers=auth(token), params=params)


def test_metadata_pages_search_and_no_content_or_row_values(library):
    tid, rid = seed(library)
    aids = populate(library, tid, rid, 7)
    first = page(library, tid, limit=2)
    assert first.status_code == 200, first.text
    assert [a["attachment_id"] for a in first.json()["data"]] == aids[:2]
    item = first.json()["data"][0]
    assert set(item) == {"attachment_id", "row_id", "filename", "media_type", "size", "created_at",
                         "metadata", "source_label"}
    assert item["source_label"] == "一致收敛" and item["size"] == 1234
    assert not list((library.app.state.settings.data_dir / "objects" / "sha256").rglob("proof.pdf"))
    seen = first.json()["data"]
    cursor = first.json()["meta"]["next_cursor"]
    while cursor:
        result = page(library, tid, limit=2, cursor=cursor).json()
        seen += result["data"]
        cursor = result["meta"]["next_cursor"]
    assert [a["attachment_id"] for a in seen] == aids
    assert len(page(library, tid, q="  PROOF.PDF  ").json()["data"]) == 7
    assert len(page(library, tid, q="一致").json()["data"]) == 7
    assert page(library, tid, q="absent").json()["data"] == []
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("UPDATE data SET values_json=? WHERE _row_id=?", (json.dumps({"title": "Straße"}), rid))
    assert len(page(library, tid, q="STRASSE").json()["data"]) == 7
    operation = library.get("/openapi.json").json()["paths"]["/api/v1/tables/{tid}/attachments"]["get"]
    assert "xelibrary:read" in operation["description"]
    assert {p["name"] for p in operation["parameters"]} >= {"tid", "q", "limit", "cursor"}


def test_bounded_scan_empty_page_then_readable_matches(library):
    tid, hidden = seed(library)
    visible = library.post(f"/api/v1/tables/{tid}/rows", headers=auth(),
                           json={"values": {"title": "Readable source"}}).json()["data"]["_row_id"]
    populate(library, tid, hidden, 500)
    aids = populate(library, tid, visible, 8, start=501)
    assert put_acl(library, "table", tid, {"bob": ["read"], "alice": ["read", "attach"]}).status_code == 200
    assert put_acl(library, "row", f"{tid}:{hidden}", {"alice": ["read"]}).status_code == 200
    result = page(library, tid, "bob", limit=100, q="readable").json()
    assert result["data"] == [] and result["meta"]["next_cursor"]
    result = page(library, tid, "bob", limit=100, q="readable", cursor=result["meta"]["next_cursor"]).json()
    assert [a["attachment_id"] for a in result["data"]] == aids
    assert result["meta"]["next_cursor"] is None
    assert "一致收敛" not in json.dumps(result, ensure_ascii=False)


def test_current_acl_scope_and_identity_are_checked_on_every_page(library):
    tid, rid = seed(library)
    populate(library, tid, rid, 3)
    assert put_acl(library, "table", tid, {"bob": ["read"]}).status_code == 200
    result = page(library, tid, "bob", limit=1).json()
    cursor = result["meta"]["next_cursor"]
    assert put_acl(library, "row", f"{tid}:{rid}", {"alice": ["read"]}).status_code == 200
    hidden = page(library, tid, "bob", cursor=cursor)
    assert hidden.status_code == 200 and hidden.json()["data"] == []
    assert put_acl(library, "row", f"{tid}:{rid}", {"bob": ["read"]}).status_code == 200
    library.app.state.settings.dev_tokens["bob"]["scope"] = "xelibrary:attach"
    assert page(library, tid, "bob", cursor=cursor).status_code == 403
    library.app.state.settings.dev_tokens["bob"]["scope"] = "xelibrary:read"
    assert len(page(library, tid, "bob", cursor=cursor).json()["data"]) == 2
    assert put_acl(library, "table", tid, {"alice": ["read"]}).status_code == 200
    assert page(library, tid, "bob", cursor=cursor).status_code == 403
    assert page(library, tid, "opencode", cursor=cursor).status_code == 403


def test_deleted_orphan_rows_and_other_tables_are_not_candidates(library):
    tid, rid = seed(library)
    aids = populate(library, tid, rid, 1)
    other = library.post(f"/api/v1/tables/{tid}/rows", headers=auth(),
                         json={"values": {"title": "Deleted private source"}}).json()["data"]["_row_id"]
    populate(library, tid, other, 1, start=2)
    populate(library, tid, str(UUID(int=999)), 1, start=3)
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("UPDATE data SET _deleted_at='2026-01-01' WHERE _row_id=?", (other,))
    result = page(library, tid).json()
    assert [a["attachment_id"] for a in result["data"]] == aids
    assert "Deleted private source" not in json.dumps(result)
    table = library.post("/api/v1/tables", headers=auth(), json={"path": "/Other", "name": "Other",
        "columns": [{"name": "name", "type": "string"}]}).json()["data"]["table_id"]
    row = library.post(f"/api/v1/tables/{table}/rows", headers=auth(),
                       json={"values": {"name": "Other table"}}).json()["data"]["_row_id"]
    other_aid = populate(library, table, row, 1, start=4)[0]
    assert other_aid not in [a["attachment_id"] for a in page(library, tid).json()["data"]]
    assert library.post(f"/api/v1/tables/{tid}/rows/{rid}/attachments", headers=auth(),
                        json={"attachment_id": other_aid}).status_code == 404


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 101}, {"q": "x" * 201},
                                    {"cursor": "?"}, {"cursor": "x" * 2049}])
def test_invalid_list_bounds(library, params):
    tid, _ = seed(library)
    assert page(library, tid, **params).status_code == 422


def test_cursor_bound_to_search_and_table_and_shape(library):
    tid, rid = seed(library)
    populate(library, tid, rid, 2)
    cursor = page(library, tid, limit=1).json()["meta"]["next_cursor"]
    assert page(library, tid, q="proof", cursor=cursor).status_code == 422
    body = json.loads(base64.urlsafe_b64decode(cursor))
    for field, value in [("v", True), ("table", "another-table"), ("after", "not-a-uuid")]:
        bad = body | {field: value}
        forged = base64.urlsafe_b64encode(json.dumps(bad).encode()).decode()
        assert page(library, tid, cursor=forged).status_code == 422
    for bad in [[], None, {"v": 1}, body | {"extra": 1}]:
        forged = base64.urlsafe_b64encode(json.dumps(bad).encode()).decode()
        assert page(library, tid, cursor=forged).status_code == 422


def test_existing_binding_scope_acl_copy_and_same_row_semantics(library):
    tid, source = seed(library)
    uploaded = library.post("/api/v1/attachments", params={"table_id": tid, "row_id": source},
                            headers=auth(), files={"upload": ("proof.pdf", b"%PDF-synthetic")}).json()["data"]
    aid = uploaded["attachment_id"]
    target = library.post(f"/api/v1/tables/{tid}/rows", headers=auth(),
                         json={"values": {"title": "Target"}}).json()["data"]["_row_id"]
    assert put_acl(library, "table", tid, {"bob": ["read"], "alice": ["read", "attach"]}).status_code == 200
    assert len(page(library, tid, "bob").json()["data"]) == 1
    url = f"/api/v1/tables/{tid}/rows/{target}/attachments"
    assert library.post(url, headers=auth("bob"), json={"attachment_id": aid}).status_code == 403
    first = library.post(url, headers=auth(), json={"attachment_id": aid}).json()["data"]
    second = library.post(url, headers=auth(), json={"attachment_id": aid}).json()["data"]
    assert first["attachment_id"] != second["attachment_id"] != aid
    same = library.post(f"/api/v1/tables/{tid}/rows/{source}/attachments", headers=auth(),
                        json={"attachment_id": aid})
    assert same.status_code == 200 and same.json()["data"]["attachment_id"] == aid
    assert len(library.get(url, headers=auth()).json()["data"]) == 2
    assert len(library.get(f"/api/v1/tables/{tid}/rows/{source}/attachments", headers=auth()).json()["data"]) == 1
    assert library.get(f"/api/v1/attachments/{first['attachment_id']}/content", params={"table_id": tid},
                       headers=auth()).content == b"%PDF-synthetic"
    assert len(list((library.app.state.settings.data_dir / "objects" / "sha256").rglob("*"))) == 3
    assert put_acl(library, "row", f"{tid}:{target}", {"alice": ["read"]}).status_code == 200
    assert library.post(url, headers=auth(), json={"attachment_id": aid}).status_code == 403
    assert put_acl(library, "row", f"{tid}:{source}", {"alice": ["attach"]}).status_code == 200
    assert page(library, tid).json()["data"] and all(a["row_id"] == target for a in page(library, tid).json()["data"])


def test_source_label_priority_clip_and_row_id_fallback(library):
    tid, rid = seed(library)
    populate(library, tid, rid, 1)
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        for position, name in [(2, "name"), (3, "body")]:
            db.execute("INSERT INTO __xl_schema VALUES(?,?,?)", (name, position,
                       json.dumps({"name": name, "type": "string"})))
        db.execute("UPDATE data SET values_json=? WHERE _row_id=?", (json.dumps({
            "title": "  ", "name": "Display name", "body": "Unrelated extra column"}), rid))
    item = page(library, tid).json()["data"][0]
    assert item["source_label"] == "Display name"
    assert page(library, tid, q="Unrelated extra").json()["data"] == []
    assert "Unrelated extra column" not in json.dumps(item)
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("UPDATE data SET values_json=? WHERE _row_id=?", (json.dumps({"title": "A" * 256 + "suffix"}), rid))
    assert page(library, tid).json()["data"][0]["source_label"] == "A" * 256
    assert page(library, tid, q="suffix").json()["data"] == []
    with sqlite3.connect(library.app.state.settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("UPDATE data SET values_json=? WHERE _row_id=?", (json.dumps({"title": None}), rid))
    assert page(library, tid).json()["data"][0]["source_label"] == rid
    assert len(page(library, tid, q=rid).json()["data"]) == 1


def test_keyset_continuity_with_tied_times_interleaved_acl_and_insert_delete(library):
    tid, visible = seed(library)
    hidden = library.post(f"/api/v1/tables/{tid}/rows", headers=auth(),
                         json={"values": {"title": "Hidden"}}).json()["data"]["_row_id"]
    aids = populate(library, tid, visible, 905)
    storage = library.app.state.settings.data_dir / "tables" / f"{tid}.xl"
    with sqlite3.connect(storage) as db:
        db.executemany("UPDATE __xl_attachments SET row_id=? WHERE attachment_id=?",
                       [(hidden, aid) for i, aid in enumerate(aids, 1) if i % 2 == 0])
    assert put_acl(library, "table", tid, {"bob": ["read"]}).status_code == 200
    assert put_acl(library, "row", f"{tid}:{hidden}", {"alice": ["read"]}).status_code == 200
    result = page(library, tid, "bob", limit=100).json()
    seen = [a["attachment_id"] for a in result["data"]]
    cursor = result["meta"]["next_cursor"]
    # New references behind the keyset appear on a fresh query; remaining references
    # retain a stable order even when an earlier source reference is removed.
    populate(library, tid, visible, 1, start=0)
    with sqlite3.connect(storage) as db:
        db.execute("DELETE FROM __xl_attachments WHERE attachment_id=?", (aids[0],))
    while cursor:
        result = page(library, tid, "bob", limit=100, cursor=cursor).json()
        seen += [a["attachment_id"] for a in result["data"]]
        cursor = result["meta"]["next_cursor"]
    assert seen == aids[::2]
    assert len(seen) == len(set(seen)) == 453
    fresh = page(library, tid, "bob", limit=1).json()
    assert fresh["data"][0]["attachment_id"] == str(UUID(int=0))
