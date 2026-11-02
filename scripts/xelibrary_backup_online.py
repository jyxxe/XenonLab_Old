#!/usr/bin/env python3
"""Create, verify and retain best-effort online XeLibrary snapshots (issue #390).

A timer cannot rely on an operator pausing writes, so this mode copies each
SQLite payload through the online backup API, copies the content-addressed
objects, then runs the full manifest verification. If a concurrent catalogue or
file change (table create/move/delete, attachment binding, GC) makes the
snapshot inconsistent, verification fails, the partial snapshot is removed and
the next attempt runs; a snapshot that passes `verify_backup` is internally
consistent. It is still a **local** copy, not an offsite backup.

Usage:

    python scripts/xelibrary_backup_online.py snapshot \
      --source /srv/xelibrary/core/xeonolab \
      --root /var/backups/xeonolab/xelibrary --keep 7

    python scripts/xelibrary_backup_online.py verify --root /var/backups/xeonolab/xelibrary
    python scripts/xelibrary_backup_online.py prune  --root /var/backups/xeonolab/xelibrary --keep 7
"""

from __future__ import annotations

import argparse
import datetime
import re
import shutil
import sys
import time
from pathlib import Path

from xeonolab.backup import BackupError, create_backup, verify_backup

SNAPSHOT_NAME = re.compile(r"^xelibrary-(\d{8}T\d{6}Z)(?:-\d+)?$")


def snapshot_directory_name(now: datetime.datetime | None = None, *, suffix: int | None = None) -> str:
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"xelibrary-{stamp}" if suffix is None else f"xelibrary-{stamp}-{suffix}"


def snapshot_directories(root: Path) -> list[Path]:
    """Return existing snapshot directories, newest first (never symlinks)."""
    if not root.is_dir():
        return []
    entries = []
    for entry in root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        if SNAPSHOT_NAME.fullmatch(entry.name):
            entries.append(entry)
    entries.sort(key=lambda path: path.name, reverse=True)
    return entries


def prune_snapshots(root: Path, keep: int) -> list[Path]:
    """Delete all but the newest ``keep`` snapshots; returns the removed paths."""
    if keep < 1:
        raise BackupError("keep must be at least one snapshot")
    existing = snapshot_directories(root)
    removed = existing[keep:]
    for entry in removed:
        # Defensive re-check: only direct, non-symlink snapshot directories.
        if entry.is_symlink() or entry.parent.resolve() != root.resolve():
            raise BackupError(f"refusing to prune unsafe entry: {entry}")
        shutil.rmtree(entry)
    return removed


def create_online_snapshot(source: Path, root: Path, *, keep: int = 7, attempts: int = 3,
                           sleep_seconds: float = 2.0) -> dict:
    if attempts < 1:
        raise BackupError("attempts must be at least one")
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        destination = root / snapshot_directory_name(suffix=None if attempt == 1 else attempt)
        try:
            create_backup(source, destination, online=True)
            manifest = verify_backup(destination)
            removed = prune_snapshots(root, keep)
            return {
                "snapshot": str(destination),
                "attempts": attempt,
                "tables": len(manifest.get("tables") or []),
                "files": len(manifest.get("files") or []),
                "pruned": [str(path) for path in removed],
            }
        except BackupError as error:
            last_error = error
            if destination.exists() and not destination.is_symlink():
                shutil.rmtree(destination, ignore_errors=True)
            if attempt < attempts:
                time.sleep(sleep_seconds)
    raise BackupError(f"online snapshot failed after {attempts} attempts: {last_error}")


def newest_snapshot(root: Path) -> Path | None:
    snapshots = snapshot_directories(root)
    return snapshots[0] if snapshots else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Online XeLibrary snapshot/verify/retention (issue #390).")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot", help="create a verified online snapshot and prune old ones")
    snapshot.add_argument("--source", type=Path, required=True)
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--keep", type=int, default=7)
    snapshot.add_argument("--attempts", type=int, default=3)
    verify = sub.add_parser("verify", help="verify the newest snapshot")
    verify.add_argument("--root", type=Path, required=True)
    prune = sub.add_parser("prune", help="retain only the newest snapshots")
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--keep", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = create_online_snapshot(args.source, args.root, keep=args.keep, attempts=args.attempts)
            print(f"snapshot ok: {result['snapshot']} (attempt {result['attempts']}, "
                  f"{result['tables']} tables, {result['files']} files, pruned {len(result['pruned'])})")
        elif args.command == "verify":
            target = newest_snapshot(args.root)
            if target is None:
                raise BackupError("no snapshot found")
            manifest = verify_backup(target)
            print(f"verified {target}: {len(manifest['files'])} files, {len(manifest.get('tables') or [])} tables")
        else:
            removed = prune_snapshots(args.root, args.keep)
            print(f"pruned {len(removed)} snapshot(s)")
        return 0
    except (BackupError, OSError) as error:
        print(f"backup error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
