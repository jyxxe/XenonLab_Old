"""Verified Keycloak PostgreSQL dump tooling (issue #390)."""

import json
import pathlib
import subprocess

import pytest

from infra.systemd import postgres_backup

MAGIC = b"PGDMP"


def fake_success(command, timeout=600):
    if "pg_restore" in command:
        return subprocess.CompletedProcess(command, 0, "; archive\n1; 0 0 TABLE public user\n", "")
    return subprocess.CompletedProcess(command, 0, "", "")


def fake_dump_stdout(command, destination, timeout=600):
    """The real helper pipes pg_dump stdout into a root-owned 0600 file."""
    assert "pg_dump" in command
    destination.write_bytes(MAGIC + b"payload")
    destination.chmod(0o600)
    return subprocess.CompletedProcess(command, 0, "", "")


def test_snapshot_writes_manifest_and_verifies(tmp_path, monkeypatch):
    monkeypatch.setattr(postgres_backup, "run", fake_success)
    monkeypatch.setattr(postgres_backup, "run_to_file", fake_dump_stdout)
    result = postgres_backup.create_snapshot("keycloak", tmp_path / "backups", keep=2)
    snapshot = pathlib.Path(result["snapshot"])
    dump = snapshot / "keycloak.dump"
    assert dump.read_bytes() == MAGIC + b"payload"
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["format"] == "keycloak-postgres-backup-v1"
    assert manifest["database"] == "keycloak"
    assert manifest["objects"] == 1
    verified = postgres_backup.verify_snapshot(snapshot)
    assert verified["size"] == len(MAGIC + b"payload")


def test_dump_failure_leaves_no_snapshot(tmp_path, monkeypatch):
    def failing(command, destination, timeout=600):
        return subprocess.CompletedProcess(command, 2, "", "connection refused")

    monkeypatch.setattr(postgres_backup, "run_to_file", failing)
    with pytest.raises(postgres_backup.PostgresBackupError):
        postgres_backup.create_snapshot("keycloak", tmp_path / "backups", attempts=2)
    remaining = [path for path in (tmp_path / "backups").iterdir() if path.is_dir() and not path.name.startswith("keycloak-dump-")]
    assert remaining == []


def test_validate_rejects_non_custom_archive(tmp_path, monkeypatch):
    monkeypatch.setattr(postgres_backup, "run", fake_success)
    bad = tmp_path / "bad.dump"
    bad.write_bytes(b"plain text")
    with pytest.raises(postgres_backup.PostgresBackupError, match="custom-format"):
        postgres_backup.validate_dump(bad)


def test_run_to_file_captures_stdout_as_private_file(tmp_path):
    destination = tmp_path / "archive.dump"
    result = postgres_backup.run_to_file(["/bin/sh", "-c", "printf PGDMPpayload"], destination)
    assert result.returncode == 0
    assert destination.read_bytes() == MAGIC + b"payload"
    assert (destination.stat().st_mode & 0o777) == 0o600


def test_prune_keeps_newest_and_ignores_foreign_entries(tmp_path):
    root = tmp_path / "backups"
    root.mkdir()
    for name in ("keycloak-20260101T000000Z", "keycloak-20260102T000000Z", "keycloak-20260103T000000Z", "other"):
        (root / name).mkdir()
    removed = postgres_backup.prune_snapshots(root, keep=2)
    assert [path.name for path in removed] == ["keycloak-20260101T000000Z"]
    remaining = sorted(path.name for path in postgres_backup.snapshot_directories(root))
    assert remaining == ["keycloak-20260102T000000Z", "keycloak-20260103T000000Z"]


def test_snapshot_name_is_stamped_and_collision_safe():
    import datetime
    moment = datetime.datetime(2026, 11, 2, 4, 30, 0, tzinfo=datetime.UTC)
    assert postgres_backup.snapshot_name(moment) == "keycloak-20261102T043000Z"
    assert postgres_backup.snapshot_name(moment, suffix=2) == "keycloak-20261102T043000Z-2"
