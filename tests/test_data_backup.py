"""Verified data-disk tar snapshot tooling (issue #390)."""

import datetime
import json
import os
import tarfile

import pytest

from xeonolab import data_backup


def make_source(root):
    source = root / "data"
    (source / "nested").mkdir(parents=True)
    (source / "top.txt").write_text("top")
    (source / "nested" / "child.txt").write_text("child")
    (source / "lost+found").mkdir()
    (source / "lost+found" / "junk").write_text("ignored")
    os.symlink("top.txt", source / "link.txt")
    return source


def test_snapshot_creates_verified_archive(tmp_path):
    source = make_source(tmp_path)
    result = data_backup.create_snapshot(source, tmp_path / "backups", keep=2)
    snapshot = data_backup.Path(result["snapshot"])
    assert (snapshot / "data.tar.gz").is_file()
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["format"] == "data-disk-backup-v1"
    assert manifest["source"] == str(source)
    verified = data_backup.verify_snapshot(snapshot)
    assert verified["entries"] == result["entries"]
    with tarfile.open(snapshot / "data.tar.gz", "r:gz") as archive:
        names = {member.name for member in archive}
    assert "top.txt" in names
    assert "nested/child.txt" in names
    assert not any(name.startswith("lost+found") for name in names)
    assert "link.txt" in names


def test_verify_rejects_tampered_archive(tmp_path):
    source = make_source(tmp_path)
    result = data_backup.create_snapshot(source, tmp_path / "backups")
    snapshot = data_backup.Path(result["snapshot"])
    with (snapshot / "data.tar.gz").open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(data_backup.DataBackupError, match="checksum"):
        data_backup.verify_snapshot(snapshot)


def test_verify_rejects_unexpected_entry(tmp_path):
    source = make_source(tmp_path)
    result = data_backup.create_snapshot(source, tmp_path / "backups")
    snapshot = data_backup.Path(result["snapshot"])
    (snapshot / "stray.txt").write_text("x")
    with pytest.raises(data_backup.DataBackupError, match="unexpected entries"):
        data_backup.verify_snapshot(snapshot)


def test_prune_keeps_newest_and_ignores_foreign_entries(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    for name in ("data-20260101T000000Z", "data-20260102T000000Z", "data-20260103T000000Z", "other"):
        (root / name).mkdir()
    removed = data_backup.prune_snapshots(root, keep=2)
    assert [path.name for path in removed] == ["data-20260101T000000Z"]
    assert sorted(path.name for path in data_backup.snapshot_directories(root)) == [
        "data-20260102T000000Z",
        "data-20260103T000000Z",
    ]


def test_snapshot_requires_existing_source(tmp_path):
    with pytest.raises(data_backup.DataBackupError, match="data root is missing"):
        data_backup.create_snapshot(tmp_path / "absent", tmp_path / "backups")


def test_snapshot_name_is_stamped_and_collision_safe():
    moment = datetime.datetime(2026, 11, 3, 4, 45, 0, tzinfo=datetime.UTC)
    assert data_backup.snapshot_name(moment) == "data-20261103T044500Z"
    assert data_backup.snapshot_name(moment, suffix=2) == "data-20261103T044500Z-2"


def test_unit_has_no_release_only_condition():
    """Non-release targets (VM110) must be able to run the timer."""
    unit = data_backup.Path(__file__).parents[1] / "infra/systemd/xeonolab-data-backup.service"
    text = unit.read_text()
    assert "ConditionPathExists=/usr/local/libexec/xeonolab-data-backup.sh" in text
    conditions = [line for line in text.splitlines() if line.startswith("ConditionPathExists=")]
    assert not any("/opt/xeonolab" in line for line in conditions), conditions
