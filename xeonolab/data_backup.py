"""Verified daily tar snapshots of mounted data disks (issue #390).

The production VMs keep their durable bulk data on separate PVE data disks that
are mounted inside the guest (for example ``/srv/dev`` on the Forgejo host and
``/srv/control`` on the web host). Those disks are not part of the per-domain
application backups and their virtual size (200 GiB–8 TiB) makes a VM-level
vzdump impractical, while the *actual* content is only hundreds of MiB.

This module snapshots one mounted data root as a compressed tar archive and
publishes it only after verification:

- archive a single mounted data root (no cross-device traversal), skipping
  ``lost+found`` and special files that tar cannot represent safely;
- write a per-snapshot ``manifest.json``/``manifest.sha256`` with the archive
  SHA-256, size, entry count and skipped special-file count;
- verify the gzip/tar stream, the archive hash and the entry count before the
  snapshot is published, then prune older ``data-<UTC stamp>Z`` snapshots.

This is a **local** snapshot on the same host, not an offsite backup.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
from pathlib import Path

FORMAT = "data-disk-backup-v1"
NAME = re.compile(r"^data-(\d{8}T\d{6}Z)(?:-\d+)?$")
MANIFEST_NAMES = {"manifest.json", "manifest.sha256"}
EXCLUDED_TOP_LEVEL = {"lost+found"}
DEFAULT_MAX_ENTRIES = 2_000_000


class DataBackupError(RuntimeError):
    pass


def snapshot_name(now: datetime.datetime | None = None, *, suffix: int | None = None) -> str:
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"data-{stamp}" if suffix is None else f"data-{stamp}-{suffix}"


def snapshot_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    entries = [entry for entry in root.iterdir()
               if entry.is_dir() and not entry.is_symlink() and NAME.fullmatch(entry.name)]
    entries.sort(key=lambda path: path.name, reverse=True)
    return entries


def prune_snapshots(root: Path, keep: int) -> list[Path]:
    if keep < 1:
        raise DataBackupError("keep must be at least one snapshot")
    removed = snapshot_directories(root)[keep:]
    for entry in removed:
        if entry.is_symlink() or entry.parent.resolve() != root.resolve():
            raise DataBackupError(f"refusing to prune unsafe entry: {entry}")
        shutil.rmtree(entry)
    return removed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_member(info: tarfile.TarInfo) -> bool:
    """Skip top-level lost+found and file types tar cannot restore safely."""
    first = Path(info.name).parts[0] if Path(info.name).parts else ""
    if first in EXCLUDED_TOP_LEVEL:
        return False
    return info.isreg() or info.isdir() or info.issym() or info.islnk()


def create_archive(source: Path, destination: Path, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> dict:
    """Stream ``source`` into ``destination``; returns entry and skipped counts."""
    entries = 0
    skipped = 0
    with tarfile.open(destination, "w:gz", compresslevel=6) as archive:
        archive.add(str(source), arcname=".", recursive=False)
        for path in sorted(source.rglob("*")):
            try:
                info = archive.gettarinfo(str(path), arcname=str(path.relative_to(source)))
            except OSError:
                skipped += 1
                continue
            if not _archive_member(info):
                skipped += 1
                continue
            entries += 1
            if entries > max_entries:
                raise DataBackupError(f"data root has more than {max_entries} entries")
            if info.isreg():
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
            else:
                archive.addfile(info)
    os.chmod(destination, 0o600)
    return {"entries": entries, "skipped_special": skipped}


def verify_archive(archive_path: Path, *, expected_entries: int | None = None) -> int:
    if archive_path.is_symlink() or not archive_path.is_file():
        raise DataBackupError(f"archive is missing or unsafe: {archive_path}")
    count = 0
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            for member in archive:
                if member.name == ".":
                    continue
                if not _archive_member(member):
                    continue
                count += 1
    except (tarfile.TarError, OSError) as error:
        raise DataBackupError(f"archive is not a valid gzip tar stream: {error}") from error
    if expected_entries is not None and count != expected_entries:
        raise DataBackupError(f"archive entry count mismatch: {count} != {expected_entries}")
    return count


def _write_manifest(snapshot: Path, metadata: dict) -> None:
    raw = (json.dumps(metadata, indent=2) + "\n").encode()
    (snapshot / "manifest.json").write_bytes(raw)
    os.chmod(snapshot / "manifest.json", 0o600)
    (snapshot / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    os.chmod(snapshot / "manifest.sha256", 0o600)


def verify_snapshot(snapshot: Path) -> dict:
    snapshot = Path(snapshot)
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise DataBackupError(f"snapshot is missing or unsafe: {snapshot}")
    expected = {"data.tar.gz", *MANIFEST_NAMES}
    actual = {entry.name for entry in snapshot.iterdir()}
    if actual != expected:
        raise DataBackupError(f"snapshot has missing or unexpected entries: {sorted(actual ^ expected)}")
    for entry in snapshot.iterdir():
        if entry.is_symlink():
            raise DataBackupError(f"snapshot contains a symlink: {entry.name}")
    manifest_path = snapshot / "manifest.json"
    sidecar = snapshot / "manifest.sha256"
    raw = manifest_path.read_bytes()
    if sidecar.read_text().strip() != hashlib.sha256(raw).hexdigest():
        raise DataBackupError("manifest checksum mismatch")
    manifest = json.loads(raw)
    if manifest.get("format") != FORMAT:
        raise DataBackupError("unsupported snapshot format")
    archive = snapshot / manifest["archive"]
    if _sha256(archive) != manifest["sha256"]:
        raise DataBackupError("archive checksum mismatch")
    entries = verify_archive(archive, expected_entries=manifest["entries"])
    return {"source": manifest["source"], "entries": entries,
            "size": manifest["size"], "skipped_special": manifest.get("skipped_special", 0)}


def create_snapshot(source: Path, root: Path, *, keep: int = 7, attempts: int = 2,
                    max_entries: int = DEFAULT_MAX_ENTRIES,
                    free_space_reserve_bytes: int = 64 * 1024 * 1024) -> dict:
    source = Path(source).absolute()
    root = Path(root).absolute()
    if source == root or source.is_relative_to(root) or root.is_relative_to(source):
        raise DataBackupError("snapshot root must be separate from the data root")
    if source.is_symlink() or not source.is_dir():
        raise DataBackupError(f"data root is missing or unsafe: {source}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        snapshot = root / snapshot_name(suffix=None if attempt == 1 else attempt)
        staging = Path(tempfile.mkdtemp(prefix=".data-dump-", dir=str(root)))
        try:
            os.chmod(staging, 0o700)
            # A rough preflight only; the archive is verified before publishing.
            free = shutil.disk_usage(root).free
            estimate = sum(path.stat().st_size for path in source.rglob("*") if path.is_file())
            if free < estimate + free_space_reserve_bytes:
                raise DataBackupError(
                    f"insufficient free space: need {estimate + free_space_reserve_bytes}, have {free}"
                )
            archive_path = staging / "data.tar.gz"
            counts = create_archive(source, archive_path, max_entries=max_entries)
            metadata = {
                "format": FORMAT,
                "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
                "source": str(source),
                "archive": "data.tar.gz",
                "size": archive_path.stat().st_size,
                "sha256": _sha256(archive_path),
                "entries": counts["entries"],
                "skipped_special": counts["skipped_special"],
            }
            _write_manifest(staging, metadata)
            verified = verify_snapshot(staging)
            os.replace(staging, snapshot)
            removed = prune_snapshots(root, keep)
            return {"snapshot": str(snapshot), "attempt": attempt,
                    "pruned": [str(path) for path in removed], **verified}
        except (DataBackupError, OSError, tarfile.TarError) as error:
            last_error = error
            shutil.rmtree(staging, ignore_errors=True)
            if attempt >= attempts:
                break
    raise DataBackupError(f"data snapshot failed after {attempts} attempts: {last_error}")
