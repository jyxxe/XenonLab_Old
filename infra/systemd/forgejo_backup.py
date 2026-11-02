#!/usr/bin/python3
"""Nightly verified snapshot of the Forgejo data root (issue #390).

VM110 hosts the Forgejo instance (repository hosting for this project) without
any scheduled backup. The data root is small (~110 MB: SQLite database, git
repositories, attachments), so a short stop-copy-start window is safe and gives
a consistent snapshot without cross-file races:

- stop the Forgejo unit, copy the data root and ``app.ini`` (logs excluded),
  always start the unit again (even when copying fails);
- verify every copied SQLite database with ``PRAGMA integrity_check`` and run
  ``git fsck --no-progress`` for each repository;
- write a ``manifest.json``/``manifest.sha256`` (path, size and SHA-256 per
  file) and prune old ``forgejo-<UTC stamp>Z`` snapshots beyond the retention
  count.

This is a **local** snapshot on the same host, not an offsite backup.

Usage:

    python3 infra/systemd/forgejo_backup.py snapshot \
      --source /var/lib/forgejo --config /etc/forgejo/app.ini \
      --root /var/backups/xeonolab/forgejo --keep 7 --service forgejo
    python3 infra/systemd/forgejo_backup.py verify --dir /var/backups/xeonolab/forgejo/forgejo-...Z
    python3 infra/systemd/forgejo_backup.py prune --root /var/backups/xeonolab/forgejo --keep 7
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

NAME = re.compile(r"^forgejo-(\d{8}T\d{6}Z)(?:-\d+)?$")
EXCLUDED_TOP_LEVEL = {"log"}


class ForgejoBackupError(RuntimeError):
    pass


def snapshot_name(now: datetime.datetime | None = None, *, suffix: int | None = None) -> str:
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"forgejo-{stamp}" if suffix is None else f"forgejo-{stamp}-{suffix}"


def snapshot_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    entries = [entry for entry in root.iterdir()
               if entry.is_dir() and not entry.is_symlink() and NAME.fullmatch(entry.name)]
    entries.sort(key=lambda path: path.name, reverse=True)
    return entries


def prune_snapshots(root: Path, keep: int) -> list[Path]:
    if keep < 1:
        raise ForgejoBackupError("keep must be at least one snapshot")
    removed = snapshot_directories(root)[keep:]
    for entry in removed:
        if entry.is_symlink() or entry.parent.resolve() != root.resolve():
            raise ForgejoBackupError(f"refusing to prune unsafe entry: {entry}")
        shutil.rmtree(entry)
    return removed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def copy_source(source: Path, destination: Path, config: Path | None) -> None:
    destination.mkdir(parents=True, mode=0o700)
    shutil.copytree(
        source,
        destination / "forgejo",
        symlinks=True,
        ignore=shutil.ignore_patterns(*EXCLUDED_TOP_LEVEL),
    )
    if config is not None and config.is_file():
        shutil.copyfile(config, destination / "app.ini")
    for directory, _, files in os.walk(destination):
        for name in files:
            os.chmod(os.path.join(directory, name), 0o600)


def verify_directory(snapshot: Path, *, check_git: bool = True) -> dict:
    """Validate copied databases and repositories; returns repository count."""
    root = snapshot / "forgejo"
    if not (root / "data").is_dir():
        raise ForgejoBackupError(f"snapshot has no data directory: {snapshot}")
    databases = sorted(root.rglob("*.db"))
    if not databases:
        raise ForgejoBackupError(f"snapshot has no SQLite database: {snapshot}")
    for database in databases:
        try:
            import sqlite3

            with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
                result = db.execute("PRAGMA integrity_check").fetchone()
        except Exception as error:
            raise ForgejoBackupError(f"cannot verify {database.relative_to(snapshot)}: {error}") from None
        if result is None or result[0] != "ok":
            raise ForgejoBackupError(f"integrity check failed for {database.relative_to(snapshot)}")
    repositories = sorted(root.glob("repositories/*/*.git"))
    if check_git:
        for repository in repositories:
            try:
                result = subprocess.run(
                    ["git", "-C", str(repository), "fsck", "--no-progress"],
                    capture_output=True, text=True, timeout=120,
                )
            except (OSError, subprocess.TimeoutExpired) as error:
                raise ForgejoBackupError(f"git fsck failed for {repository.name}: {error}") from None
            if result.returncode:
                raise ForgejoBackupError(f"git fsck failed for {repository.name}: {result.stderr.strip()[:200]}")
    return {"databases": len(databases), "repositories": len(repositories)}


def write_manifest(snapshot: Path, metadata: dict) -> Path:
    files = []
    for path in sorted(snapshot.rglob("*")):
        if path.is_symlink():
            raise ForgejoBackupError(f"symlink in snapshot: {path}")
        if path.is_file():
            files.append({
                "path": path.relative_to(snapshot).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            })
    manifest = {
        "format": "forgejo-backup-v1",
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "source": metadata.get("source"),
        "service_stopped": metadata.get("service_stopped"),
        "files": files,
        **{key: metadata[key] for key in ("databases", "repositories") if key in metadata},
    }
    raw = (json.dumps(manifest, indent=2) + "\n").encode()
    (snapshot / "manifest.json").write_bytes(raw)
    os.chmod(snapshot / "manifest.json", 0o600)
    sidecar = hashlib.sha256(raw).hexdigest() + "\n"
    (snapshot / "manifest.sha256").write_text(sidecar)
    os.chmod(snapshot / "manifest.sha256", 0o600)
    return snapshot / "manifest.json"


def systemctl(action: str, service: str) -> None:
    result = subprocess.run(["systemctl", action, service], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ForgejoBackupError(f"systemctl {action} {service} failed: {result.stderr.strip()[:200]}")


def create_snapshot(source: Path, config: Path | None, root: Path, *, keep: int = 7,
                    service: str = "forgejo", attempts: int = 2, check_git: bool = True,
                    sleep_seconds: float = 2.0) -> dict:
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        destination = root / snapshot_name(suffix=None if attempt == 1 else attempt)
        stopped = False
        try:
            systemctl("stop", service)
            stopped = True
            copy_source(source, destination, config)
        except (ForgejoBackupError, OSError) as error:
            last_error = error
            shutil.rmtree(destination, ignore_errors=True)
        finally:
            if stopped:
                try:
                    systemctl("start", service)
                except ForgejoBackupError as error:
                    raise ForgejoBackupError(f"snapshot copied but service restart failed: {error}") from error
        if last_error is None:
            try:
                counts = verify_directory(destination, check_git=check_git)
                write_manifest(destination, {"source": str(source), "service_stopped": True, **counts})
                removed = prune_snapshots(root, keep)
                return {"snapshot": str(destination), "attempts": attempt,
                        "pruned": [str(path) for path in removed], **counts}
            except (ForgejoBackupError, OSError) as error:
                last_error = error
                shutil.rmtree(destination, ignore_errors=True)
        if attempt < attempts:
            time.sleep(sleep_seconds)
    raise ForgejoBackupError(f"forgejo snapshot failed after {attempts} attempts: {last_error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified nightly Forgejo snapshot (issue #390).")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--source", type=Path, required=True)
    snapshot.add_argument("--config", type=Path, default=None)
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--keep", type=int, default=7)
    snapshot.add_argument("--service", default="forgejo")
    snapshot.add_argument("--attempts", type=int, default=2)
    verify = sub.add_parser("verify")
    verify.add_argument("--dir", dest="directory", type=Path, required=True)
    verify.add_argument("--skip-git", action="store_true")
    prune = sub.add_parser("prune")
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--keep", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = create_snapshot(args.source, args.config, args.root, keep=args.keep,
                                     service=args.service, attempts=args.attempts)
            print(f"snapshot ok: {result['snapshot']} (attempt {result['attempts']}, "
                  f"{result['databases']} db, {result['repositories']} repos, pruned {len(result['pruned'])})")
        elif args.command == "verify":
            counts = verify_directory(args.directory, check_git=not args.skip_git)
            print(f"verified {args.directory}: {counts['databases']} db, {counts['repositories']} repos")
        else:
            removed = prune_snapshots(args.root, args.keep)
            print(f"pruned {len(removed)} snapshot(s)")
        return 0
    except (ForgejoBackupError, OSError) as error:
        print(f"forgejo backup error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
