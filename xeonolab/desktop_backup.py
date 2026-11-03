"""Verified nightly snapshot of the VM101 desktop state (issue #390).

VM101 hosts the desktop application data root ``/var/lib/xeonolab/desktop-web``:
the durable ``accounts.sqlite`` (account trees, revocable API keys, audit),
``desktop.sqlite`` (preferences and wallpaper metadata) and the immutable
wallpaper files. ``browser-sessions.sqlite`` and ``rate-limits.sqlite3`` are
ephemeral runtime state and are deliberately excluded.

The snapshot is created online: SQLite's backup API produces a consistent
point-in-time copy of each database while the service keeps running, and the
existing wallpaper snapshot helper is reused for ``desktop.sqlite`` plus the
wallpaper assets it references (under the shared desktop data lock). Every
snapshot is verified before it is published, and old snapshots are pruned.

This is a **local** snapshot on the same host, not an offsite backup.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path

from .wallpaper_backup import (
    DEFAULT_FREE_SPACE_RESERVE_BYTES,
    DEFAULT_MAX_ASSETS,
    DEFAULT_MAX_TOTAL_BYTES,
    WallpaperBackupError,
    create_desktop_wallpaper_backup,
    verify_desktop_wallpaper_backup,
)

FORMAT = "desktop-backup-v1"
NAME = re.compile(r"^desktop-(\d{8}T\d{6}Z)(?:-\d+)?$")
DEFAULT_MAX_ACCOUNTS_BYTES = 64 * 1024 * 1024
MANIFEST_NAMES = {"manifest.json", "manifest.sha256"}


class DesktopBackupError(RuntimeError):
    pass


def snapshot_name(now: datetime.datetime | None = None, *, suffix: int | None = None) -> str:
    stamp = (now or datetime.datetime.now(datetime.UTC)).strftime("%Y%m%dT%H%M%SZ")
    return f"desktop-{stamp}" if suffix is None else f"desktop-{stamp}-{suffix}"


def snapshot_directories(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    entries = [entry for entry in root.iterdir()
               if entry.is_dir() and not entry.is_symlink() and NAME.fullmatch(entry.name)]
    entries.sort(key=lambda path: path.name, reverse=True)
    return entries


def prune_snapshots(root: Path, keep: int) -> list[Path]:
    if keep < 1:
        raise DesktopBackupError("keep must be at least one snapshot")
    removed = snapshot_directories(root)[keep:]
    for entry in removed:
        if entry.is_symlink() or entry.parent.resolve() != root.resolve():
            raise DesktopBackupError(f"refusing to prune unsafe entry: {entry}")
        shutil.rmtree(entry)
    return removed


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_database(source: Path, destination: Path) -> None:
    """Copy one live SQLite database consistently without a WAL sidecar."""
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst)
                mode = dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                if mode.lower() != "delete":
                    raise DesktopBackupError(
                        f"could not materialize {source.name} snapshot without WAL sidecars"
                    )
                if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise DesktopBackupError(f"{source.name} snapshot failed integrity_check")
    except sqlite3.Error as error:
        raise DesktopBackupError(f"cannot snapshot {source.name}: {error}") from error
    for suffix in ("-wal", "-shm"):
        if Path(str(destination) + suffix).exists():
            raise DesktopBackupError(f"{source.name} snapshot left an unexpected sidecar: {suffix}")
    os.chmod(destination, 0o600)


def _verify_accounts(path: Path) -> int:
    if path.is_symlink() or not path.is_file():
        raise DesktopBackupError("accounts database is missing or unsafe")
    size = path.stat().st_size
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise DesktopBackupError("accounts database failed integrity_check")
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    except sqlite3.Error as error:
        raise DesktopBackupError(f"cannot verify accounts database: {error}") from error
    if not {"accounts", "api_keys"} <= names:
        raise DesktopBackupError(f"accounts database is missing expected tables: {sorted(names)}")
    return size


def _manifest_files(snapshot: Path) -> list[dict]:
    files = []
    for path in sorted(snapshot.rglob("*")):
        if path.is_symlink():
            raise DesktopBackupError(f"symlink in snapshot: {path}")
        if path.is_file() and path.name not in MANIFEST_NAMES:
            files.append({
                "path": path.relative_to(snapshot).as_posix(),
                "size": path.stat().st_size,
                "sha256": _sha256(path),
            })
    return files


def _write_manifest(snapshot: Path, metadata: dict) -> None:
    manifest = {
        "format": FORMAT,
        "created_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "data_dir": metadata["data_dir"],
        "desktop": metadata["desktop"],
        "files": _manifest_files(snapshot),
    }
    raw = (json.dumps(manifest, indent=2) + "\n").encode()
    (snapshot / "manifest.json").write_bytes(raw)
    os.chmod(snapshot / "manifest.json", 0o600)
    (snapshot / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
    os.chmod(snapshot / "manifest.sha256", 0o600)


def verify_snapshot(
    snapshot: Path,
    *,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_assets: int = DEFAULT_MAX_ASSETS,
    max_accounts_bytes: int = DEFAULT_MAX_ACCOUNTS_BYTES,
) -> dict:
    snapshot = Path(snapshot)
    if snapshot.is_symlink() or not snapshot.is_dir():
        raise DesktopBackupError(f"snapshot is missing or unsafe: {snapshot}")
    for entry in snapshot.iterdir():
        if entry.is_symlink():
            raise DesktopBackupError(f"snapshot contains a symlink: {entry.name}")
    manifest_path = snapshot / "manifest.json"
    sidecar = snapshot / "manifest.sha256"
    if not manifest_path.is_file() or not sidecar.is_file():
        raise DesktopBackupError(f"snapshot is missing its manifest: {snapshot}")
    raw = manifest_path.read_bytes()
    if sidecar.read_text().strip() != hashlib.sha256(raw).hexdigest():
        raise DesktopBackupError("manifest checksum mismatch")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DesktopBackupError("invalid snapshot manifest") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
        raise DesktopBackupError("unsupported snapshot format")
    expected = {"accounts.sqlite", *MANIFEST_NAMES} | ({"desktop"} if manifest.get("desktop") else set())
    actual = {entry.name for entry in snapshot.iterdir()}
    if actual != expected:
        raise DesktopBackupError(f"snapshot has missing or unexpected entries: {sorted(actual ^ expected)}")
    accounts_size = _verify_accounts(snapshot / "accounts.sqlite")
    if accounts_size > max_accounts_bytes:
        raise DesktopBackupError("accounts database exceeds the configured size budget")
    wallpapers = 0
    if manifest.get("desktop"):
        try:
            desktop_manifest = verify_desktop_wallpaper_backup(
                snapshot / "desktop", max_total_bytes=max_total_bytes, max_assets=max_assets
            )
        except WallpaperBackupError as error:
            raise DesktopBackupError(f"desktop database/wallpapers are invalid: {error}") from error
        wallpapers = len(desktop_manifest["wallpapers"])
    if manifest.get("files") != _manifest_files(snapshot):
        raise DesktopBackupError("snapshot file inventory does not match the manifest")
    return {"accounts_bytes": accounts_size, "wallpapers": wallpapers,
            "files": len(manifest["files"]), "desktop": bool(manifest.get("desktop"))}


def create_snapshot(
    data_dir: Path,
    root: Path,
    *,
    keep: int = 7,
    attempts: int = 2,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_assets: int = DEFAULT_MAX_ASSETS,
    max_accounts_bytes: int = DEFAULT_MAX_ACCOUNTS_BYTES,
    free_space_reserve_bytes: int = DEFAULT_FREE_SPACE_RESERVE_BYTES,
) -> dict:
    data_dir = Path(data_dir).absolute()
    root = Path(root).absolute()
    if data_dir == root or data_dir.is_relative_to(root) or root.is_relative_to(data_dir):
        raise DesktopBackupError("snapshot root must be separate from the desktop data root")
    accounts = data_dir / "accounts.sqlite"
    desktop = data_dir / "desktop.sqlite"
    if not accounts.is_file():
        raise DesktopBackupError(f"accounts database is missing: {accounts}")
    if not desktop.is_file():
        raise DesktopBackupError(f"desktop database is missing: {desktop}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        snapshot = root / snapshot_name(suffix=None if attempt == 1 else attempt)
        staging = Path(tempfile.mkdtemp(prefix=".desktop-dump-", dir=str(root)))
        try:
            os.chmod(staging, 0o700)
            # The wallpaper helper takes the shared desktop data lock itself, so
            # no outer lock may be held here (flock is not re-entrant across
            # descriptors). accounts.sqlite is copied with SQLite's online
            # backup API, which is consistent against concurrent transactions.
            create_desktop_wallpaper_backup(
                data_dir, staging / "desktop",
                max_total_bytes=max_total_bytes,
                max_assets=max_assets,
                free_space_reserve_bytes=free_space_reserve_bytes,
            )
            _snapshot_database(accounts, staging / "accounts.sqlite")
            _write_manifest(staging, {"data_dir": str(data_dir), "desktop": True})
            verified = verify_snapshot(
                staging, max_total_bytes=max_total_bytes, max_assets=max_assets,
                max_accounts_bytes=max_accounts_bytes,
            )
            os.replace(staging, snapshot)
            removed = prune_snapshots(root, keep)
            return {"snapshot": str(snapshot), "attempt": attempt,
                    "pruned": [str(path) for path in removed], **verified}
        except (DesktopBackupError, WallpaperBackupError, OSError) as error:
            last_error = error
            shutil.rmtree(staging, ignore_errors=True)
            if attempt >= attempts:
                break
    raise DesktopBackupError(f"desktop snapshot failed after {attempts} attempts: {last_error}")
