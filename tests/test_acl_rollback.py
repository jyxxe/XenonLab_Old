import json
import sqlite3

import pytest

from scripts.rollback_acl_schema import RollbackError, rollback_acl_database


def current_catalogue(path):
    with sqlite3.connect(path) as db:
        db.execute(
            "CREATE TABLE acl(resource_type TEXT,resource_id TEXT,grants TEXT,"
            "revision INTEGER NOT NULL DEFAULT 1,explicit INTEGER NOT NULL DEFAULT 1,"
            "PRIMARY KEY(resource_type,resource_id))"
        )
        db.execute(
            "INSERT INTO acl VALUES(?,?,?,?,?)",
            ("path", "/inherited", json.dumps({"alice": ["read"]}), 4, 1),
        )
        db.execute(
            "INSERT INTO acl VALUES(?,?,?,?,?)",
            ("table", "table-1", "{}", 3, 0),
        )
        db.execute(
            "INSERT INTO acl VALUES(?,?,?,?,?)",
            ("row", "table-1:row-1", json.dumps({"bob": ["read", "attach"]}), 2, 1),
        )


def test_downgrade_backups_drops_tombstones_and_restores_old_insert(tmp_path):
    catalogue = tmp_path / "catalogue.sqlite"
    backup = tmp_path / "catalogue-before-rollback.sqlite"
    current_catalogue(catalogue)

    result = rollback_acl_database(catalogue, backup, service_stopped=True)

    assert result["retained_explicit_grants"] == 2
    assert backup.exists()
    with sqlite3.connect(backup) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(acl)")] == [
            "resource_type", "resource_id", "grants", "revision", "explicit"
        ]
        assert db.execute("SELECT count(*) FROM acl WHERE explicit=0").fetchone() == (1,)
    with sqlite3.connect(catalogue) as db:
        assert [row[1] for row in db.execute("PRAGMA table_info(acl)")] == [
            "resource_type", "resource_id", "grants"
        ]
        retained = dict((row[0], json.loads(row[1])) for row in db.execute("SELECT resource_id,grants FROM acl"))
        assert retained == {
            "/inherited": {"alice": ["read"]},
            "table-1:row-1": {"bob": ["read", "attach"]},
        }
        # The legacy release inserts values without naming columns.
        db.execute("INSERT INTO acl VALUES(?,?,?)", ("table", "legacy-write", '{"carol":["read"]}'))
        assert db.execute("SELECT count(*) FROM acl").fetchone() == (3,)


def test_downgrade_requires_stop_confirmation_and_leaves_catalogue_untouched(tmp_path):
    catalogue = tmp_path / "catalogue.sqlite"
    backup = tmp_path / "backup.sqlite"
    current_catalogue(catalogue)

    with pytest.raises(RollbackError, match="stop XeLibrary"):
        rollback_acl_database(catalogue, backup, service_stopped=False)

    assert not backup.exists()
    with sqlite3.connect(catalogue) as db:
        assert len(list(db.execute("PRAGMA table_info(acl)"))) == 5


def test_failed_downgrade_rolls_back_schema_and_keeps_backup(tmp_path):
    catalogue = tmp_path / "catalogue.sqlite"
    backup = tmp_path / "backup.sqlite"
    current_catalogue(catalogue)
    with sqlite3.connect(catalogue) as db:
        db.execute("UPDATE acl SET grants='not-json' WHERE resource_id='/inherited'")

    with pytest.raises(RollbackError, match="invalid grants JSON"):
        rollback_acl_database(catalogue, backup, service_stopped=True)

    assert backup.exists()
    with sqlite3.connect(catalogue) as db:
        assert len(list(db.execute("PRAGMA table_info(acl)"))) == 5
        assert db.execute("SELECT count(*) FROM acl").fetchone() == (3,)
