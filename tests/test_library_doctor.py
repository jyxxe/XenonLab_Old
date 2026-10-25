"""Issue #81 first increment: read-only XeLibrary consistency scan."""

import hashlib
import shutil
import sqlite3
from uuid import uuid4

from xeonolab.library_doctor import scan_library

from .conftest import auth


def make_table(library, name="一致性扫描"):
    created = library.post(
        "/api/v1/tables",
        headers=auth(),
        json={"path": "/一致性", "name": name, "columns": [{"name": "title", "type": "string"}]},
    )
    assert created.status_code == 201, created.text
    table_id = created.json()["data"]["table_id"]
    row = library.post(f"/api/v1/tables/{table_id}/rows", headers=auth(), json={"values": {"title": "v1"}})
    assert row.status_code == 201, row.text
    return table_id, row.json()["data"]["_row_id"]


def add_attachment(root, table_id, row_id, digest, attachment_id="att-1", size=7):
    with sqlite3.connect(root / "tables" / f"{table_id}.xl") as db:
        db.execute(
            "INSERT INTO __xl_attachments VALUES(?,?,?,?,?,?,?,?)",
            (attachment_id, row_id, digest, "proof.png", "image/png", size, "2020-01-01T00:00:00Z", "{}"),
        )


def put_object(root, digest, data=b"payload"):
    path = root / "objects" / "sha256" / digest[:2] / digest[2:4] / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def scan(library, admin=True):
    response = library.get("/api/v1/maintenance/consistency", headers=auth("admin" if admin else "alice"))
    return response


def test_consistency_scan_requires_admin_and_reports_clean_state(library):
    make_table(library)
    assert scan(library, admin=False).status_code == 403

    response = scan(library)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["mode"] == "read_only"
    assert data["ok"] is True
    assert data["issues"] == []
    assert data["issues_truncated"] is False
    assert data["critical_issues"] == 0
    assert data["counts"]["catalogue_tables"] == 1
    assert data["counts"]["table_files"] == 1
    assert data["object_store"]["count"] == 0
    assert data["object_store"]["unreferenced"] == 0
    assert data["object_store"]["orphan_check_complete"] is True


def test_scan_detects_missing_and_orphan_table_files(library, tmp_path):
    table_id, _ = make_table(library)
    orphan_id = str(uuid4())
    shutil.copyfile(tmp_path / "tables" / f"{table_id}.xl", tmp_path / "tables" / f"{orphan_id}.xl")
    (tmp_path / "tables" / f"{table_id}.xl").rename(tmp_path / "detached.xl")

    data = scan(library).json()["data"]
    kinds = {issue["kind"] for issue in data["issues"]}
    assert "catalogue_table_file_missing" in kinds
    assert "orphan_table_file" in kinds
    assert data["ok"] is False
    # The scan reports; it must not restore or delete anything.
    assert not (tmp_path / "tables" / f"{table_id}.xl").exists()
    assert (tmp_path / "tables" / f"{orphan_id}.xl").exists()
    assert data["object_store"]["orphan_check_complete"] is False
    assert data["object_store"]["unreferenced"] is None


def test_scan_detects_interrupted_table_delete(library, tmp_path):
    table_id, _ = make_table(library)
    (tmp_path / "tables" / f"{table_id}.xl").rename(tmp_path / "tables" / f".{table_id}.xl.deleting")

    data = scan(library).json()["data"]
    issue = next(issue for issue in data["issues"] if issue["kind"] == "interrupted_table_delete")
    assert issue["severity"] == "warning"
    assert issue["table_id"] == table_id
    assert data["counts"]["table_tombstones"] == 1


def test_scan_detects_deletion_marker_and_path_drift(library, tmp_path):
    table_id, _ = make_table(library)
    with sqlite3.connect(tmp_path / "tables" / f"{table_id}.xl") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(__xl_meta)")}
        if "deleting" not in columns:
            db.execute("ALTER TABLE __xl_meta ADD COLUMN deleting INTEGER NOT NULL DEFAULT 0")
        db.execute("UPDATE __xl_meta SET deleting=1, logical_path='/other'")

    data = scan(library).json()["data"]
    kinds = {issue["kind"] for issue in data["issues"]}
    assert "table_deletion_marker" in kinds
    assert "table_path_mismatch" in kinds
    assert data["ok"] is True  # both are recoverable warnings, not critical


def test_scan_detects_identity_mismatch(library, tmp_path):
    table_id, _ = make_table(library)
    with sqlite3.connect(tmp_path / "tables" / f"{table_id}.xl") as db:
        db.execute("UPDATE __xl_meta SET table_id=?", (str(uuid4()),))

    data = scan(library).json()["data"]
    issue = next(issue for issue in data["issues"] if issue["kind"] == "table_identity_mismatch")
    assert issue["severity"] == "critical"
    assert issue["table_id"] == table_id
    assert data["ok"] is False


def test_scan_detects_attachment_reference_problems(library, tmp_path):
    table_id, row_id = make_table(library)
    present = "a" * 64
    absent = "b" * 64
    put_object(tmp_path, present)
    add_attachment(tmp_path, table_id, row_id, present, attachment_id="att-good")
    add_attachment(tmp_path, table_id, row_id, absent, attachment_id="att-missing")
    add_attachment(tmp_path, table_id, row_id, "not-a-digest", attachment_id="att-invalid")
    add_attachment(tmp_path, table_id, "missing-row", present, attachment_id="att-rowless")
    orphan = "c" * 64
    put_object(tmp_path, orphan, b"12345")

    data = scan(library).json()["data"]
    kinds = {issue["kind"] for issue in data["issues"]}
    assert "missing_attachment_object" in kinds
    assert "invalid_attachment_reference" in kinds
    assert "attachment_row_missing" in kinds
    assert data["counts"]["attachment_references"] == 4
    assert data["ok"] is False
    assert data["object_store"]["orphan_check_complete"] is True
    assert data["object_store"]["unreferenced"] == 1
    assert data["object_store"]["unreferenced_bytes"] == 5


def test_scan_counts_orphan_objects_and_never_writes(library, tmp_path):
    table_id, _ = make_table(library)
    put_object(tmp_path, "d" * 64, b"orphan-bytes")

    def user_data():
        paths = list((tmp_path / "tables").glob("*.xl")) + list((tmp_path / "objects" / "sha256").rglob("*"))
        return {
            path.relative_to(tmp_path): (path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in paths
            if path.is_file()
        }

    before = user_data()
    report = scan_library(tmp_path)
    assert report["object_store"]["unreferenced"] == 1
    assert report["object_store"]["unreferenced_bytes"] == len(b"orphan-bytes")
    assert user_data() == before
    # SQLite may refresh WAL shared-memory sidecars for a read-only connection;
    # the scanner must never add or remove canonical table files.
    assert [path.name for path in sorted((tmp_path / "tables").glob("*.xl"))] == [f"{table_id}.xl"]


def test_scan_reports_unreadable_table_file(library, tmp_path):
    table_id, _ = make_table(library)
    (tmp_path / "tables" / f"{table_id}.xl").write_bytes(b"not a sqlite database")

    data = scan(library).json()["data"]
    issue = next(issue for issue in data["issues"] if issue["kind"] == "table_file_unreadable")
    assert issue["severity"] == "critical"
    assert data["ok"] is False
    assert data["object_store"]["orphan_check_complete"] is False
    assert data["object_store"]["unreferenced"] is None


def test_scan_detects_upload_state_leftovers(library, tmp_path):
    with sqlite3.connect(tmp_path / "uploads.sqlite") as db:
        db.execute(
            "INSERT INTO uploads(upload_id,attachment_id,actor,table_id,row_id,filename,size,"
            "part_size,part_count,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (str(uuid4()), str(uuid4()), "alice", str(uuid4()), "row", "a.bin", 1, 1, 1, "2020-01-01T00:00:00Z"),
        )
    staging = tmp_path / "objects" / ".uploads"
    (staging / str(uuid4())).mkdir(parents=True)
    (staging / f".{uuid4()}.tmp").write_bytes(b"partial")

    data = scan(library).json()["data"]
    kinds = {issue["kind"] for issue in data["issues"]}
    assert "upload_table_missing" in kinds
    assert "orphan_upload_staging" in kinds
    assert "stale_upload_temp" in kinds
    assert data["counts"]["uploads"] == 1


def test_scan_truncates_issue_list(library, tmp_path):
    for _ in range(3):
        (tmp_path / "tables" / f"{uuid4()}.xl").write_bytes(b"")

    report = scan_library(tmp_path, max_issues=1)
    assert report["issues_truncated"] is True
    assert len(report["issues"]) == 1
    assert report["issues"][0]["kind"] == "orphan_table_file"
    assert report["counts"]["table_files"] == 3


def test_scan_reports_unreadable_catalogue(library, tmp_path):
    (tmp_path / "catalogue.sqlite").write_bytes(b"not a sqlite database")

    report = scan_library(tmp_path)
    assert report["ok"] is False
    assert [issue["kind"] for issue in report["issues"]] == ["catalogue_unreadable"]
