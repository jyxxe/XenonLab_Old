#!/usr/bin/python3
"""Nightly verified PostgreSQL dump for the Keycloak database (issue #390).

VM109 hosts Keycloak plus its PostgreSQL 18 cluster (``keycloak`` database,
~13 MB). It had no scheduled backup. ``pg_dump -Fc`` is transactionally
consistent on a live database, so no service stop is needed:

- run ``sudo -n -u postgres pg_dump -Fc`` (peer auth; no credential is read or
  written by this tool) and stream its archive from stdout into a root-owned
  0600 staging file, so the database account never needs access to the private
  backup tree;
- validate the archive with ``pg_restore -l`` and the ``PGDMP`` magic, record
  size/SHA-256 in a per-snapshot ``manifest.json``/``manifest.sha256``;
- retain the newest N ``keycloak-<UTC stamp>Z`` snapshot directories.

Restore is an operator drill (see docs/runbooks/keycloak-backup-restore.md):
restore into a temporary database, check the object list, then drop it. This is
a **local** snapshot on the same host, not an offsite backup.

Usage:

    python3 infra/systemd/postgres_backup.py snapshot --database keycloak \
      --root /var/backups/xeonolab/keycloak --keep 7
    python3 infra/systemd/postgres_backup.py verify --dir <snapshot>
    python3 infra/systemd/postgres_backup.py prune --root ... --keep 7
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
import tempfile
from pathlib import Path

NAME = re.compile(r"^keycloak-(\d{8}T\d{6}Z)(?:-\d+)?$")
MAGIC = b"PGDMP"


class PostgresBackupError(RuntimeError):
    pass


def snapshot_name(now: datetime.datetime | None = None, *, suffix: int | None = None) -> str:
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"keycloak-{stamp}" if suffix is None else f"keycloak-{stamp}-{suffix}"


def snapshot_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    entries = [entry for entry in root.iterdir()
               if entry.is_dir() and not entry.is_symlink() and NAME.fullmatch(entry.name)]
    entries.sort(key=lambda path: path.name, reverse=True)
    return entries


def prune_snapshots(root: Path, keep: int) -> list[Path]:
    if keep < 1:
        raise PostgresBackupError("keep must be at least one snapshot")
    removed = snapshot_directories(root)[keep:]
    for entry in removed:
        if entry.is_symlink() or entry.parent.resolve() != root.resolve():
            raise PostgresBackupError(f"refusing to prune unsafe entry: {entry}")
        shutil.rmtree(entry)
    return removed


def run(command: list[str], timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout)


def run_to_file(command: list[str], destination: Path, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run a dump command and capture its binary stdout into a private file.

    ``pg_dump`` runs as the database account via peer auth, but writes its
    archive to stdout. The root process owns the destination file, so the
    database account never needs traversal or write access to the private
    backup tree and the archive bytes are created 0600 by the caller.
    """
    with destination.open("wb") as handle:
        result = subprocess.run(command, stdout=handle, stderr=subprocess.PIPE, timeout=timeout)
    os.chmod(destination, 0o600)
    result.stderr = result.stderr.decode(errors="replace") if isinstance(result.stderr, bytes) else result.stderr
    return result


def dump_database(database: str, destination: Path, *, run_as: str = "postgres") -> None:
    command = ["sudo", "-n", "-u", run_as, "pg_dump", "-Fc", database]
    result = run_to_file(command, destination)
    if result.returncode:
        raise PostgresBackupError(f"pg_dump failed: {(result.stderr or '').strip()[:300]}")


def validate_dump(path: Path) -> int:
    if not path.is_file():
        raise PostgresBackupError(f"dump is missing: {path}")
    with path.open("rb") as handle:
        if handle.read(len(MAGIC)) != MAGIC:
            raise PostgresBackupError("dump is not a PostgreSQL custom-format archive")
    # Listing the archive TOC is offline work: the root process can read its
    # own 0600 dump, so no database account permission is needed here.
    result = run(["pg_restore", "-l", str(path)])
    if result.returncode:
        raise PostgresBackupError(f"pg_restore -l failed: {result.stderr.strip()[:300]}")
    entries = [line for line in result.stdout.splitlines() if line and not line.startswith(";")]
    if not entries:
        raise PostgresBackupError("dump archive lists no objects")
    return len(entries)


def write_manifest(snapshot: Path, dump: Path, metadata: dict) -> None:
    raw = dump.read_bytes()
    manifest = {
        "format": "keycloak-postgres-backup-v1",
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "database": metadata["database"],
        "dump": dump.name,
        "size": len(raw),
        "objects": metadata.get("objects"),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    payload = (json.dumps(manifest, indent=2) + "\n").encode()
    (snapshot / "manifest.json").write_bytes(payload)
    os.chmod(snapshot / "manifest.json", 0o600)
    (snapshot / "manifest.sha256").write_text(hashlib.sha256(payload).hexdigest() + "\n")
    os.chmod(snapshot / "manifest.sha256", 0o600)


def verify_snapshot(snapshot: Path) -> dict:
    manifest_path = snapshot / "manifest.json"
    sidecar = snapshot / "manifest.sha256"
    if not manifest_path.is_file() or not sidecar.is_file():
        raise PostgresBackupError(f"snapshot is missing its manifest: {snapshot}")
    raw = manifest_path.read_bytes()
    if sidecar.read_text().strip() != hashlib.sha256(raw).hexdigest():
        raise PostgresBackupError("manifest checksum mismatch")
    manifest = json.loads(raw)
    if manifest.get("format") != "keycloak-postgres-backup-v1":
        raise PostgresBackupError("unsupported manifest format")
    dump = snapshot / manifest["dump"]
    if hashlib.sha256(dump.read_bytes()).hexdigest() != manifest["sha256"]:
        raise PostgresBackupError("dump checksum mismatch")
    objects = validate_dump(dump)
    return {"size": manifest["size"], "objects": objects, "sha256": manifest["sha256"]}


def create_snapshot(database: str, root: Path, *, keep: int = 7, run_as: str = "postgres",
                    attempts: int = 2) -> dict:
    root = root.absolute()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        snapshot = root / snapshot_name(suffix=None if attempt == 1 else attempt)
        snapshot.mkdir(mode=0o700)
        staging = Path(tempfile.mkdtemp(prefix="keycloak-dump-", dir=str(root)))
        try:
            staged = staging / "keycloak.dump"
            dump_database(database, staged, run_as=run_as)
            objects = validate_dump(staged)
            destination = snapshot / "keycloak.dump"
            shutil.copyfile(staged, destination)
            os.chmod(destination, 0o600)
            write_manifest(snapshot, destination, {"database": database, "objects": objects})
            removed = prune_snapshots(root, keep)
            return {"snapshot": str(snapshot), "attempts": attempt, "objects": objects,
                    "size": destination.stat().st_size,
                    "pruned": [str(path) for path in removed]}
        except (PostgresBackupError, OSError) as error:
            last_error = error
            shutil.rmtree(snapshot, ignore_errors=True)
            if attempt >= attempts:
                break
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    raise PostgresBackupError(f"keycloak snapshot failed after {attempts} attempts: {last_error}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verified nightly Keycloak PostgreSQL dump (issue #390).")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--database", default="keycloak")
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--keep", type=int, default=7)
    snapshot.add_argument("--run-as", default="postgres")
    verify = sub.add_parser("verify")
    verify.add_argument("--dir", dest="directory", type=Path, required=True)
    verify.add_argument("--run-as", default="postgres")
    prune = sub.add_parser("prune")
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--keep", type=int, default=7)
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = create_snapshot(args.database, args.root, keep=args.keep, run_as=args.run_as)
            print(f"snapshot ok: {result['snapshot']} (attempt {result['attempts']}, "
                  f"{result['size']} bytes, {result['objects']} objects, pruned {len(result['pruned'])})")
        elif args.command == "verify":
            result = verify_snapshot(args.directory)
            print(f"verified {args.directory}: {result['size']} bytes, {result['objects']} objects")
        else:
            removed = prune_snapshots(args.root, args.keep)
            print(f"pruned {len(removed)} snapshot(s)")
        return 0
    except (PostgresBackupError, OSError, ValueError) as error:
        print(f"keycloak backup error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
