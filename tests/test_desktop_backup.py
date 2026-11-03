"""Verified desktop state snapshot tooling (issue #390)."""

import datetime
import sqlite3
import uuid

import pytest
from PIL import Image

from xeonolab import desktop_backup


def _write_webp(path, size=(2, 2)):
    Image.new("RGB", size, (12, 34, 56)).save(path, format="WEBP")
    return path.stat().st_size


def make_data_dir(root, *, wallpapers=1):
    data = root / "desktop-web"
    data.mkdir()
    with sqlite3.connect(data / "accounts.sqlite") as db:
        db.execute("CREATE TABLE accounts(uid TEXT PRIMARY KEY)")
        db.execute("CREATE TABLE api_keys(kid TEXT PRIMARY KEY)")
        db.execute("INSERT INTO accounts VALUES('owner')")
    with sqlite3.connect(data / "desktop.sqlite") as db:
        db.execute("CREATE TABLE preferences(owner TEXT PRIMARY KEY, definition TEXT NOT NULL)")
        db.execute(
            "CREATE TABLE desktop_wallpapers(owner TEXT NOT NULL, asset_id TEXT NOT NULL,"
            " media_type TEXT NOT NULL, size INTEGER NOT NULL)"
        )
        db.execute("INSERT INTO preferences VALUES('owner', '{}')")
    (data / "wallpapers").mkdir()
    for index in range(wallpapers):
        asset = str(uuid.UUID(int=index + 1))
        size = _write_webp(data / "wallpapers" / f"{asset}.webp")
        with sqlite3.connect(data / "desktop.sqlite") as db:
            db.execute(
                "INSERT INTO desktop_wallpapers VALUES(?,?,?,?)",
                (f"owner{index}", asset, "image/webp", size),
            )
    # Ephemeral runtime files must never be copied into the snapshot.
    (data / "browser-sessions.sqlite").write_bytes(b"ephemeral")
    return data


def test_snapshot_creates_verified_manifest(tmp_path):
    data = make_data_dir(tmp_path)
    result = desktop_backup.create_snapshot(data, tmp_path / "backups", keep=2)
    snapshot = tmp_path / "backups" / "desktop-20261103T041500Z"
    # A real run uses the current UTC stamp; locate the single published snapshot.
    published = [p for p in (tmp_path / "backups").iterdir() if p.is_dir()]
    assert len(published) == 1
    snapshot = published[0]
    assert result["wallpapers"] == 1
    assert result["desktop"] is True
    assert (snapshot / "accounts.sqlite").is_file()
    assert not (snapshot / "browser-sessions.sqlite").exists()
    verified = desktop_backup.verify_snapshot(snapshot)
    assert verified["accounts_bytes"] > 0
    assert verified["wallpapers"] == 1


def test_snapshot_prunes_old_snapshots(tmp_path):
    data = make_data_dir(tmp_path)
    root = tmp_path / "backups"
    desktop_backup.create_snapshot(data, root, keep=1)
    for stamp in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
        (root / f"desktop-{stamp}").mkdir()
    (root / "foreign").mkdir()
    result = desktop_backup.create_snapshot(data, root, keep=2)
    names = sorted(path.name for path in root.iterdir())
    assert "foreign" in names
    assert len([name for name in names if name.startswith("desktop-")]) == 2
    # keep=2 keeps the new snapshot plus the first run's snapshot; the three
    # explicitly created older dirs are pruned.
    assert len(result["pruned"]) == 3


def test_verify_rejects_tampered_accounts(tmp_path):
    data = make_data_dir(tmp_path)
    result = desktop_backup.create_snapshot(data, tmp_path / "backups")
    snapshot = desktop_backup.Path(result["snapshot"])
    with (snapshot / "accounts.sqlite").open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(desktop_backup.DesktopBackupError, match="inventory"):
        desktop_backup.verify_snapshot(snapshot)


def test_snapshot_requires_accounts_database(tmp_path):
    data = make_data_dir(tmp_path)
    (data / "accounts.sqlite").unlink()
    with pytest.raises(desktop_backup.DesktopBackupError, match="accounts database is missing"):
        desktop_backup.create_snapshot(data, tmp_path / "backups")


def test_verify_rejects_unexpected_entry(tmp_path):
    data = make_data_dir(tmp_path)
    result = desktop_backup.create_snapshot(data, tmp_path / "backups")
    snapshot = desktop_backup.Path(result["snapshot"])
    (snapshot / "stray.txt").write_text("x")
    with pytest.raises(desktop_backup.DesktopBackupError, match="unexpected entries"):
        desktop_backup.verify_snapshot(snapshot)


def test_snapshot_name_is_stamped_and_collision_safe():
    moment = datetime.datetime(2026, 11, 3, 4, 15, 0, tzinfo=datetime.UTC)
    assert desktop_backup.snapshot_name(moment) == "desktop-20261103T041500Z"
    assert desktop_backup.snapshot_name(moment, suffix=2) == "desktop-20261103T041500Z-2"
