"""Admin batch import endpoint: atomic, bounded and idempotent (Issue #261)."""

import pytest

from .conftest import auth


def create_table(library):
    created = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/批量导入",
            "name": "批量导入",
            "columns": [
                {"name": "a_number", "type": "string", "nullable": False},
                {"name": "terms", "type": "text", "nullable": False},
            ],
        },
    )
    assert created.status_code == 201, created.text
    return created.json()["data"]["table_id"]


def batch(library, table_id, rows, token="admin"):
    return library.post(f"/api/v1/tables/{table_id}/rows:importBatch", headers=auth(token), json={"rows": rows})


def list_rows(library, table_id):
    response = library.get(f"/api/v1/tables/{table_id}/rows?limit=10", headers=auth("admin"))
    assert response.status_code == 200, response.text
    return response.json()["data"]


def test_batch_import_is_atomic_and_idempotent(library):
    table_id = create_table(library)
    rows = [
        {"key": "A000001", "values": {"a_number": "A000001", "terms": "0,1"}},
        {"key": "A000002", "values": {"a_number": "A000002", "terms": "1,2"}},
    ]
    first = batch(library, table_id, rows)
    assert first.status_code == 200, first.text
    assert first.json()["data"] == {"table_id": table_id, "imported": 2, "existing": 0, "total": 2}
    assert len(list_rows(library, table_id)) == 2

    replay = batch(library, table_id, rows)
    assert replay.status_code == 200, replay.text
    assert replay.json()["data"]["imported"] == 0
    assert replay.json()["data"]["existing"] == 2
    assert len(list_rows(library, table_id)) == 2


def test_batch_conflict_rolls_back_the_whole_batch(library):
    table_id = create_table(library)
    assert batch(
        library, table_id, [{"key": "A000001", "values": {"a_number": "A000001", "terms": "0,1"}}]
    ).status_code == 200

    conflict = batch(
        library,
        table_id,
        [
            {"key": "A000002", "values": {"a_number": "A000002", "terms": "1,2"}},
            {"key": "A000001", "values": {"a_number": "A000001", "terms": "changed"}},
        ],
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    values = [row["values"]["a_number"] for row in list_rows(library, table_id)]
    assert values == ["A000001"]


def test_batch_import_requires_admin_and_is_bounded(library):
    table_id = create_table(library)
    rows = [{"key": "A000001", "values": {"a_number": "A000001", "terms": "0"}}]
    assert batch(library, table_id, rows, token="alice").status_code == 403
    oversized = [
        {"key": f"A{i:06d}", "values": {"a_number": f"A{i:06d}", "terms": "1"}}
        for i in range(1, 1002)
    ]
    response = batch(library, table_id, oversized)
    assert response.status_code == 422
    empty = batch(library, table_id, [])
    assert empty.status_code == 422


def test_batch_import_appends_to_cjk_index_without_replacement_scan(library, monkeypatch):
    """Fresh rows must not run the O(table rows) CJK replace path.

    `index_cjk_row` deletes by an UNINDEXED column, which FTS5 implements as a
    full scan. Batch import only creates new rows, so it has to append instead;
    otherwise import cost grows superlinearly with the table size.
    """
    import xeonolab.library as library_module

    table_id = create_table(library)
    replacement_calls = []
    original = library_module.index_cjk_row

    def tracked(db, row_id, values):
        replacement_calls.append(row_id)
        return original(db, row_id, values)

    monkeypatch.setattr(library_module, "index_cjk_row", tracked)
    rows = [
        {"key": f"A{i:06d}", "values": {"a_number": f"A{i:06d}", "terms": "积分 交换"}}
        for i in range(1, 4)
    ]
    response = batch(library, table_id, rows)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["imported"] == 3
    assert replacement_calls == []

    hits = library.post("/api/v1/search/text", headers=auth("admin"), json={"query": "积分"})
    assert hits.status_code == 200, hits.text
    assert {hit["table_id"] for hit in hits.json()["data"]} == {table_id}


def test_batch_cjk_append_failure_rolls_back_the_whole_batch(library, monkeypatch):
    import xeonolab.library as library_module

    table_id = create_table(library)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected CJK append failure")

    monkeypatch.setattr(library_module, "append_cjk_row", fail)
    rows = [{"key": "A000001", "values": {"a_number": "A000001", "terms": "1"}}]
    with pytest.raises(RuntimeError, match="injected CJK append failure"):
        batch(library, table_id, rows)
    assert list_rows(library, table_id) == []

