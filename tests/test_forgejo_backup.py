"""Verified Forgejo snapshot tool (issue #390)."""

import json
import pathlib
import sqlite3

import pytest

from infra.systemd import forgejo_backup


def make_source(tmp_path: pathlib.Path) -> pathlib.Path:
    source = tmp_path / "forgejo"
    (source / "data").mkdir(parents=True)
    (source / "repositories/xenon-admin/xeonolab.git").mkdir(parents=True)
    (source / "repositories/xenon-admin/xeonolab.git/config").write_text("[core]\n")
    (source / "log").mkdir()
    (source / "log/forgejo.log").write_text("noise\n")
    with sqlite3.connect(source / "data/forgejo.db") as db:
        db.execute("CREATE TABLE repository(id INTEGER PRIMARY KEY, name TEXT)")
        db.execute("INSERT INTO repository VALUES(1, 'xeonolab')")
    config = tmp_path / "app.ini"
    config.write_text("[server]\nROOT_URL = https://example.invalid/\n")
    return source


def test_snapshot_copies_verifies_and_manifests(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    config = tmp_path / "app.ini"
    calls = []
    monkeypatch.setattr(forgejo_backup, "systemctl", lambda action, service: calls.append((action, service)))
    result = forgejo_backup.create_snapshot(
        source, config, tmp_path / "backups", keep=2, service="forgejo", check_git=False,
    )
    snapshot = pathlib.Path(result["snapshot"])
    assert snapshot.is_dir()
    assert (snapshot / "forgejo/data/forgejo.db").is_file()
    assert (snapshot / "app.ini").is_file()
    assert not (snapshot / "forgejo/log").exists()
    assert result["databases"] == 1 and result["repositories"] == 1
    assert calls == [("stop", "forgejo"), ("start", "forgejo")]
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["format"] == "forgejo-backup-v1"
    sidecar = (snapshot / "manifest.sha256").read_text().strip()
    assert len(sidecar) == 64
    assert all(item["sha256"] for item in manifest["files"])


def test_verify_rejects_a_corrupt_copied_database(tmp_path, monkeypatch):
    source = make_source(tmp_path)
    monkeypatch.setattr(forgejo_backup, "systemctl", lambda action, service: None)

    original_copy = forgejo_backup.copy_source

    def corrupt_copy(source_arg, destination, config_arg):
        original_copy(source_arg, destination, config_arg)
        (destination / "forgejo/data/forgejo.db").write_bytes(b"not a sqlite database")

    monkeypatch.setattr(forgejo_backup, "copy_source", corrupt_copy)
    with pytest.raises(forgejo_backup.ForgejoBackupError, match="cannot verify"):
        forgejo_backup.create_snapshot(
            source, None, tmp_path / "backups", attempts=1, check_git=False,
        )
    # A failed snapshot must not remain on disk.
    assert list((tmp_path / "backups").glob("forgejo-*")) == []


def test_snapshot_restarts_service_when_copying_fails(tmp_path, monkeypatch):
    source = tmp_path / "missing-source"
    calls = []
    monkeypatch.setattr(forgejo_backup, "systemctl", lambda action, service: calls.append(action))
    with pytest.raises(forgejo_backup.ForgejoBackupError):
        forgejo_backup.create_snapshot(source, None, tmp_path / "backups", attempts=1)
    assert calls == ["stop", "start"]


def test_prune_keeps_newest_and_ignores_foreign_entries(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    for name in ("forgejo-20260101T000000Z", "forgejo-20260102T000000Z", "forgejo-20260103T000000Z", "other"):
        (root / name).mkdir()
    removed = forgejo_backup.prune_snapshots(root, keep=2)
    assert [path.name for path in removed] == ["forgejo-20260101T000000Z"]
    remaining = sorted(path.name for path in forgejo_backup.snapshot_directories(root))
    assert remaining == ["forgejo-20260102T000000Z", "forgejo-20260103T000000Z"]
    assert (root / "other").is_dir()


def test_snapshot_name_is_stamped_and_collision_safe():
    import datetime
    moment = datetime.datetime(2026, 11, 2, 4, 5, 6, tzinfo=datetime.UTC)
    assert forgejo_backup.snapshot_name(moment) == "forgejo-20261102T040506Z"
    assert forgejo_backup.snapshot_name(moment, suffix=2) == "forgejo-20261102T040506Z-2"
