"""Read-only path tree listing derived from visible tables (issue #6 increment)."""

from __future__ import annotations

from .conftest import auth, put_acl


def create_table(client, path: str, name: str):
    result = client.post(
        "/api/v1/tables",
        headers=auth("alice"),
        json={"path": path, "name": name, "columns": [{"name": "body", "type": "text", "nullable": True}]},
    )
    assert result.status_code == 201, result.text
    return result.json()["data"]["table_id"]


def test_lists_visible_child_paths_with_counts(library):
    create_table(library, "/数学/分析", "一致收敛")
    create_table(library, "/数学/代数", "群论")
    create_table(library, "/物理", "力学")

    result = library.get("/api/v1/paths", headers=auth("alice"))
    assert result.status_code == 200, result.text
    nodes = result.json()["data"]
    assert [node["path"] for node in nodes] == ["/数学", "/物理"]
    maths = nodes[0]
    assert maths["name"] == "数学"
    assert maths["parent"] == "/"
    assert maths["table_count"] == 2  # subtree count of visible tables
    assert maths["has_children"] is True
    assert nodes[1]["table_count"] == 1 and nodes[1]["has_children"] is False

    nested = library.get("/api/v1/paths", params={"parent": "/数学"}, headers=auth("alice"))
    assert nested.status_code == 200, nested.text
    assert sorted(node["path"] for node in nested.json()["data"]) == ["/数学/代数", "/数学/分析"]


def test_path_listing_respects_table_visibility(library):
    create_table(library, "/数学/分析", "一致收敛")
    hidden = library.get("/api/v1/paths", headers=auth("bob"))
    assert hidden.status_code == 200, hidden.text
    assert hidden.json()["data"] == []

    grant = put_acl(library, "path", "/数学", {"bob": ["read"]})
    assert grant.status_code == 200, grant.text
    visible = library.get("/api/v1/paths", headers=auth("bob"))
    assert visible.status_code == 200, visible.text
    assert [node["path"] for node in visible.json()["data"]] == ["/数学"]


def test_path_listing_pagination_and_validation(library):
    for index in range(3):
        create_table(library, f"/p/{index}", f"T{index}")
    first = library.get("/api/v1/paths", params={"parent": "/p", "limit": 1}, headers=auth("alice"))
    assert first.status_code == 200, first.text
    payload = first.json()
    assert len(payload["data"]) == 1
    cursor = payload["meta"]["next_cursor"]
    assert cursor
    second = library.get(
        "/api/v1/paths", params={"parent": "/p", "limit": 1, "cursor": cursor}, headers=auth("alice")
    )
    assert second.status_code == 200, second.text
    assert second.json()["data"][0]["path"] > cursor

    bad = library.get("/api/v1/paths", params={"parent": "relative"}, headers=auth("alice"))
    assert bad.status_code == 422, bad.text
    assert bad.json()["error"]["code"] == "INVALID_PATH"
    limit = library.get("/api/v1/paths", params={"limit": 0}, headers=auth("alice"))
    assert limit.status_code == 422 and limit.json()["error"]["code"] == "INVALID_LIMIT"


def test_path_listing_requires_scope(library):
    result = library.get("/api/v1/paths")
    assert result.status_code == 401


def test_path_listing_openapi_documents_route(library):
    schema = library.get("/openapi.json").json()
    assert "/api/v1/paths" in schema["paths"]
    operation = schema["paths"]["/api/v1/paths"]["get"]
    assert "parent" in {parameter["name"] for parameter in operation.get("parameters", [])}
