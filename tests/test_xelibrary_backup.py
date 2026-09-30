import hashlib
import os
import sqlite3

import pytest

from xeonolab.backup import BackupError, create_backup, restore_backup, validate_data_root, verify_backup


def sample(root):
    (root / "tables").mkdir(parents=True)
    (root / "objects/sha256").mkdir(parents=True)
    catalog = sqlite3.connect(root / "catalogue.sqlite")
    catalog.execute("CREATE TABLE tables(id TEXT PRIMARY KEY,path TEXT,name TEXT,owner TEXT)")
    catalog.execute("INSERT INTO tables VALUES('t1','/docs','Docs','u1')")
    catalog.commit()
    catalog.close()
    content = b"attached data"
    digest = hashlib.sha256(content).hexdigest()
    obj = root / "objects/sha256" / digest[:2] / digest[2:4] / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(content)
    db = sqlite3.connect(root / "tables/t1.xl")
    db.executescript(
        "CREATE TABLE __xl_meta(format_version TEXT,table_id TEXT,logical_path TEXT); CREATE TABLE __xl_attachments(object_hash TEXT,size INTEGER);"
    )
    db.execute("INSERT INTO __xl_meta VALUES('1.0','t1','/docs')")
    db.execute("INSERT INTO __xl_attachments VALUES(?,?)", (digest, len(content)))
    db.commit()
    db.close()


def test_backup_verify_restore_permissions_and_content(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    manifest = verify_backup(backup)
    assert len(manifest["files"]) == 3
    assert os.stat(backup).st_mode & 0o777 == 0o700
    assert os.stat(backup / "manifest.json").st_mode & 0o777 == 0o600
    restore_root = tmp_path / "restores"
    restore_root.mkdir()
    restored = restore_backup(backup, restore_root, "drill-1")
    assert verify_backup(backup)["tables"][0]["id"] == "t1"
    assert (restored / "tables/t1.xl").is_file()
    assert os.stat(restored / "catalogue.sqlite").st_mode & 0o777 == 0o600


def test_restore_recreates_empty_tables_and_object_directories(tmp_path):
    source = tmp_path / "source"
    (source / "tables").mkdir(parents=True)
    (source / "objects/sha256").mkdir(parents=True)
    catalog = sqlite3.connect(source / "catalogue.sqlite")
    catalog.execute("CREATE TABLE tables(id TEXT PRIMARY KEY,path TEXT,name TEXT,owner TEXT)")
    catalog.commit()
    catalog.close()

    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    assert {item["path"] for item in verify_backup(backup)["files"]} == {"catalogue.sqlite"}
    restore_root = tmp_path / "restore-parent"
    restore_root.mkdir()
    restored = restore_backup(backup, restore_root, "empty")

    assert (restored / "tables").is_dir()
    assert list((restored / "tables").iterdir()) == []
    assert (restored / "objects/sha256").is_dir()
    assert list((restored / "objects/sha256").iterdir()) == []
    assert validate_data_root(restored) == []


def test_committed_wal_state_is_snapshotted_without_sidecars(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    table = source / "tables/t1.xl"
    writer = sqlite3.connect(table)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("CREATE TABLE wal_committed(value TEXT)")
    writer.execute("INSERT INTO wal_committed VALUES('committed in WAL')")
    writer.commit()
    assert (source / "tables/t1.xl-wal").exists()

    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    writer.close()
    assert not (backup / "tables/t1.xl-wal").exists()
    assert not (backup / "tables/t1.xl-shm").exists()
    restore_root = tmp_path / "restore-parent"
    restore_root.mkdir()
    restored = restore_backup(backup, restore_root, "snapshot")
    db = sqlite3.connect(restored / "tables/t1.xl")
    assert db.execute("SELECT value FROM wal_committed").fetchone() == ("committed in WAL",)
    db.close()


def test_requires_quiesce_and_rejects_existing_destination(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    with pytest.raises(BackupError, match="quiesced"):
        create_backup(source, tmp_path / "backup")
    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    with pytest.raises(BackupError, match="already exists"):
        create_backup(source, backup, quiesced=True)


def test_detects_mapping_missing_object_or_orphan_table(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    (source / "tables/orphan.xl").write_bytes(b"not a database")
    with pytest.raises(BackupError, match="table file set mismatch"):
        create_backup(source, tmp_path / "backup", quiesced=True)


def test_rejects_tampering_traversal_and_overwrite(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    with (backup / "tables/t1.xl").open("ab") as stream:
        stream.write(b"tamper")
    with pytest.raises(BackupError, match="checksum"):
        verify_backup(backup)
    # Reject traversal before target creation.
    with pytest.raises(BackupError, match="safe single"):
        restore_backup(backup, tmp_path, "../escape")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    good = create_backup(source, tmp_path / "good", quiesced=True)
    (fresh / "same").mkdir()
    with pytest.raises(BackupError, match="already exists"):
        restore_backup(good, fresh, "same")


def test_restore_cannot_target_original(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    with pytest.raises(BackupError, match="already exists"):
        restore_backup(backup, tmp_path, "source")


def test_restore_rejects_symlink_restore_root(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    with pytest.raises(BackupError, match="existing real directory"):
        restore_backup(backup, link, "copy")


def test_unreferenced_objects_are_verified_and_preserved(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    unused = b"not attached yet"
    digest = hashlib.sha256(unused).hexdigest()
    obj = source / "objects/sha256" / digest[:2] / digest[2:4] / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(unused)

    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    item = next(entry for entry in verify_backup(backup)["objects"] if entry["sha256"] == digest)
    assert item["referenced"] is False
    restore_root = tmp_path / "restore-parent"
    restore_root.mkdir()
    restored = restore_backup(backup, restore_root, "snapshot")
    assert (restored / item["path"]).read_bytes() == unused


def test_corrupt_unreferenced_object_still_fails_validation(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    unused = b"not attached yet"
    digest = hashlib.sha256(unused).hexdigest()
    obj = source / "objects/sha256" / digest[:2] / digest[2:4] / digest
    obj.parent.mkdir(parents=True)
    obj.write_bytes(b"wrong bytes")
    with pytest.raises(BackupError, match="content-addressed object hash mismatch"):
        create_backup(source, tmp_path / "backup", quiesced=True)


def test_upload_database_and_staged_parts_are_in_manifest(tmp_path):
    from uuid import uuid4

    source = tmp_path / "source"
    source.mkdir()
    sample(source)
    upload_id = str(uuid4())
    staged = source / "objects/.uploads" / upload_id / "00000001.part"
    staged.parent.mkdir(parents=True)
    staged.write_bytes(b"partial upload")
    uploads = sqlite3.connect(source / "uploads.sqlite")
    uploads.execute("CREATE TABLE upload_sessions(payload TEXT)")
    uploads.execute("INSERT INTO upload_sessions VALUES('synthetic test fixture')")
    uploads.commit()
    uploads.close()

    backup = create_backup(source, tmp_path / "backup", quiesced=True)
    manifest_paths = {entry["path"] for entry in verify_backup(backup)["files"]}
    assert "uploads.sqlite" in manifest_paths
    assert f"objects/.uploads/{upload_id}/00000001.part" in manifest_paths
    restore_root = tmp_path / "restore-parent"
    restore_root.mkdir()
    restored = restore_backup(backup, restore_root, "snapshot")
    assert (restored / "uploads.sqlite").is_file()
    assert (restored / f"objects/.uploads/{upload_id}/00000001.part").read_bytes() == b"partial upload"
