"""Online snapshot/retention logic for issue #390."""

import datetime
import pathlib

import pytest

from scripts import xelibrary_backup_online as online
from xeonolab.backup import BackupError


def make_snapshot_dirs(root: pathlib.Path, names):
    root.mkdir(parents=True, exist_ok=True)
    created = []
    for name in names:
        path = root / name
        path.mkdir()
        (path / "marker").write_text(name)
        created.append(path)
    return created


def test_snapshot_directory_name_is_stamped_and_collision_safe():
    moment = datetime.datetime(2026, 11, 2, 3, 4, 5, tzinfo=datetime.UTC)
    assert online.snapshot_directory_name(moment) == "xelibrary-20261102T030405Z"
    assert online.snapshot_directory_name(moment, suffix=2) == "xelibrary-20261102T030405Z-2"


def test_prune_keeps_newest_snapshots_and_ignores_foreign_entries(tmp_path):
    root = tmp_path / "backups"
    make_snapshot_dirs(
        root,
        [
            "xelibrary-20260101T000000Z",
            "xelibrary-20260102T000000Z",
            "xelibrary-20260103T000000Z",
            "xelibrary-20260104T000000Z",
            "not-a-snapshot",
        ],
    )
    (root / "xelibrary-20250101T000000Z").symlink_to(root / "not-a-snapshot")
    removed = online.prune_snapshots(root, keep=2)
    assert [path.name for path in removed] == [
        "xelibrary-20260102T000000Z",
        "xelibrary-20260101T000000Z",
    ]
    remaining = sorted(path.name for path in online.snapshot_directories(root))
    assert remaining == ["xelibrary-20260103T000000Z", "xelibrary-20260104T000000Z"]
    assert (root / "not-a-snapshot").is_dir()


def test_prune_requires_positive_keep(tmp_path):
    root = tmp_path / "backups"
    make_snapshot_dirs(root, ["xelibrary-20260101T000000Z"])
    with pytest.raises(BackupError, match="keep"):
        online.prune_snapshots(root, keep=0)


def test_create_online_snapshot_retries_after_a_raced_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    root = tmp_path / "backups"
    calls = {"count": 0}

    def flaky_create(source_arg, destination, *, online=False):
        calls["count"] += 1
        if calls["count"] == 1:
            destination.mkdir(parents=True)
            raise BackupError("raced table creation")
        destination.mkdir(parents=True)
        return destination

    monkeypatch.setattr(online, "create_backup", flaky_create)
    monkeypatch.setattr(online, "verify_backup", lambda path: {"tables": [{"id": "t"}], "files": [{}]})
    result = online.create_online_snapshot(source, root, keep=2, attempts=2, sleep_seconds=0)
    assert result["attempts"] == 2
    assert result["tables"] == 1
    assert pathlib.Path(result["snapshot"]).is_dir()
    assert calls["count"] == 2


def test_create_online_snapshot_raises_after_all_attempts(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()

    def always_fail(source_arg, destination, *, online=False):
        raise BackupError("catalogue churn")

    monkeypatch.setattr(online, "create_backup", always_fail)
    with pytest.raises(BackupError, match="after 2 attempts"):
        online.create_online_snapshot(source, tmp_path / "backups", attempts=2, sleep_seconds=0)
