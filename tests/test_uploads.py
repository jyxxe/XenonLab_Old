from contextlib import contextmanager

from .conftest import auth, put_acl


def seed(client):
    result = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/uploads",
            "name": "multipart",
            "columns": [{"name": "title", "type": "string", "nullable": False}],
        },
    )
    assert result.status_code == 201, result.text
    tid = result.json()["data"]["table_id"]
    result = client.post(
        f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"title": "upload target"}}
    )
    assert result.status_code == 201, result.text
    return tid, result.json()["data"]["_row_id"]


def test_multipart_upload_is_persistent_bounded_and_idempotent(library, settings):
    from fastapi.testclient import TestClient

    from xeonolab.services import create_app

    tid, rid = seed(library)
    content = b"%PDF-" + b"m" * (8 * 1024 * 1024 + 17)
    started = library.post(
        "/api/v1/uploads",
        headers=auth(),
        json={"table_id": tid, "row_id": rid, "filename": "../large.pdf", "size": len(content)},
    )
    assert started.status_code == 201, started.text
    session = started.json()["data"]
    assert session["part_count"] == 2
    upload_id = session["upload_id"]
    assert library.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth()).status_code == 409
    assert library.get(f"/api/v1/tables/{tid}/rows/{rid}/attachments", headers=auth()).json()["data"] == []
    wrong_size = library.put(f"/api/v1/uploads/{upload_id}/parts/1", headers=auth(), content=content[:-1])
    assert wrong_size.status_code == 422
    part = library.put(
        f"/api/v1/uploads/{upload_id}/parts/1",
        headers=auth(),
        content=content[: session["part_size"]],
    )
    assert part.status_code == 200 and part.json()["data"]["reused"] is False
    with TestClient(create_app("xelibrary", settings)) as restarted:
        resumed_part = restarted.put(
            f"/api/v1/uploads/{upload_id}/parts/1",
            headers=auth(),
            content=content[: session["part_size"]],
        )
        assert resumed_part.status_code == 200
        assert resumed_part.json()["data"]["reused"] is True
    duplicate = library.put(
        f"/api/v1/uploads/{upload_id}/parts/1",
        headers=auth(),
        content=content[: session["part_size"]],
    )
    assert duplicate.status_code == 200 and duplicate.json()["data"]["reused"] is True
    conflict = library.put(
        f"/api/v1/uploads/{upload_id}/parts/1",
        headers=auth(),
        content=b"x" * session["part_size"],
    )
    assert conflict.status_code == 409
    second_part = library.put(
        f"/api/v1/uploads/{upload_id}/parts/2",
        headers=auth(),
        content=content[session["part_size"] :],
    )
    assert second_part.status_code == 200
    completed = library.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth())
    assert completed.status_code == 200, completed.text
    attachment_id = completed.json()["data"]["attachment_id"]
    assert completed.json()["data"]["filename"] == "large.pdf"
    assert completed.json()["data"]["size"] == len(content)
    meta = library.get(f"/api/v1/tables/{tid}", headers=auth())
    duplicate_completion = library.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth())
    assert duplicate_completion.status_code == 200
    assert duplicate_completion.json()["data"]["attachment_id"] == attachment_id
    assert duplicate_completion.json()["meta"]["idempotent"] is True
    assert library.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"] == meta.headers["ETag"]
    assert (
        len(library.get(f"/api/v1/tables/{tid}/rows/{rid}/attachments", headers=auth()).json()["data"]) == 1
    )
    # Model a cross-file backup with a completed session/object and an older .xl snapshot.
    from xeonolab.common import transaction

    with transaction(settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("DELETE FROM __xl_attachments WHERE attachment_id=?", (attachment_id,))
        db.execute("UPDATE __xl_meta SET table_revision=table_revision-1")
    repaired = library.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth())
    assert repaired.status_code == 200
    assert repaired.json()["meta"]["repaired"] is True
    assert repaired.json()["data"]["attachment_id"] == attachment_id
    assert (
        len(library.get(f"/api/v1/tables/{tid}/rows/{rid}/attachments", headers=auth()).json()["data"]) == 1
    )
    with TestClient(create_app("xelibrary", settings)) as restarted:
        persisted = restarted.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth())
        assert persisted.status_code == 200
        assert persisted.json()["data"]["attachment_id"] == attachment_id


def test_multipart_completion_retry_does_not_duplicate_after_state_failure(library, settings, monkeypatch):
    import xeonolab.uploads as uploads

    tid, rid = seed(library)
    content = b"retriable content"
    started = library.post(
        "/api/v1/uploads",
        headers=auth(),
        json={"table_id": tid, "row_id": rid, "filename": "proof.txt", "size": len(content)},
    ).json()["data"]
    upload_id = started["upload_id"]
    assert (
        library.put(f"/api/v1/uploads/{upload_id}/parts/1", headers=auth(), content=content).status_code
        == 200
    )
    original_transaction = uploads.transaction
    state_db = settings.data_dir / "uploads.sqlite"
    state_transactions = 0

    @contextmanager
    def fail_after_completion_fact(path):
        nonlocal state_transactions
        with original_transaction(path) as db:
            yield db
            if path == state_db:
                state_transactions += 1
                # 1 claims completion, 2 persists object hash, 3 marks complete.
                # Raise inside the underlying transaction so that marker rolls back.
                if state_transactions == 3:
                    raise RuntimeError("simulated process failure before upload marker commit")

    monkeypatch.setattr(uploads, "transaction", fail_after_completion_fact)
    import pytest

    with pytest.raises(RuntimeError, match="simulated process failure"):
        library.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth())
    monkeypatch.setattr(uploads, "transaction", original_transaction)
    revision_after_fact = library.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"]
    recovered = library.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth())
    assert recovered.status_code == 200
    assert library.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"] == revision_after_fact
    assert (
        len(library.get(f"/api/v1/tables/{tid}/rows/{rid}/attachments", headers=auth()).json()["data"]) == 1
    )


def test_multipart_upload_session_acl_and_cancellation(library):
    tid, rid = seed(library)
    started = library.post(
        "/api/v1/uploads",
        headers=auth(),
        json={"table_id": tid, "row_id": rid, "filename": "x", "size": 1},
    ).json()["data"]
    upload_id = started["upload_id"]
    assert (
        library.put(f"/api/v1/uploads/{upload_id}/parts/1", headers=auth("bob"), content=b"x").status_code
        == 404
    )
    assert library.delete(f"/api/v1/uploads/{upload_id}", headers=auth()).status_code == 200
    assert (
        library.put(f"/api/v1/uploads/{upload_id}/parts/1", headers=auth(), content=b"x").status_code == 410
    )
    too_large = library.post(
        "/api/v1/uploads",
        headers=auth(),
        json={"table_id": tid, "row_id": rid, "filename": "x", "size": 2 * 1024 * 1024 * 1024 + 1},
    )
    assert too_large.status_code == 422


def test_multipart_rechecks_acl_and_scope_after_session_creation(library):
    tid, rid = seed(library)
    acl = put_acl(library, "table", tid, {"alice": ["attach"]})
    assert acl.status_code == 200, acl.text
    content = b"permission shrink"
    started = library.post(
        "/api/v1/uploads",
        headers=auth(),
        json={"table_id": tid, "row_id": rid, "filename": "acl.txt", "size": len(content)},
    ).json()["data"]
    upload_id = started["upload_id"]
    assert put_acl(library, "table", tid, {"alice": ["read"]}).status_code == 200
    denied_by_acl = library.put(f"/api/v1/uploads/{upload_id}/parts/1", headers=auth(), content=content)
    assert denied_by_acl.status_code == 403
    assert denied_by_acl.json()["error"]["code"] == "XL_FORBIDDEN"
    assert library.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth()).status_code == 403

    # A second session verifies bearer scopes are also re-evaluated on every call.
    assert put_acl(library, "table", tid, {"alice": ["attach"]}).status_code == 200
    second = library.post(
        "/api/v1/uploads",
        headers=auth(),
        json={"table_id": tid, "row_id": rid, "filename": "scope.txt", "size": len(content)},
    ).json()["data"]
    actor = library.app.state.settings.dev_tokens["alice"]
    actor["scope"] = " ".join(scope for scope in actor["scope"].split() if scope != "xelibrary:attach")
    denied_by_scope = library.put(
        f"/api/v1/uploads/{second['upload_id']}/parts/1", headers=auth(), content=content
    )
    assert denied_by_scope.status_code == 403
    assert denied_by_scope.json()["error"]["code"] == "FORBIDDEN"
    assert library.post(f"/api/v1/uploads/{second['upload_id']}/complete", headers=auth()).status_code == 403
    assert (
        library.get(f"/api/v1/tables/{tid}/rows/{rid}/attachments", headers=auth("admin")).json()["data"]
        == []
    )
