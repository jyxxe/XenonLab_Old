import hashlib
import os
import sqlite3

import pytest

from xeonolab.backup import BackupError, create_backup, restore_backup, verify_backup


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
