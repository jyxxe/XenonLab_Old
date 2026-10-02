"""Coordinated, local snapshots of desktop.sqlite and its immutable wallpaper assets.

This deliberately covers only the desktop database and wallpapers directory. It is
not a complete account/session or deployment backup.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from PIL import Image


class WallpaperBackupError(ValueError):
    pass


def _path_without_symlinks(path: Path, *, strict: bool) -> Path:
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        if current.is_symlink():
            raise WallpaperBackupError(f"symlink in path: {current}")
    try:
        return absolute.resolve(strict=strict)
    except OSError as exc:
        raise WallpaperBackupError(f"path is unavailable: {absolute}") from exc


@contextmanager
def wallpaper_data_lock(data_dir: Path):
    """Serialize wallpaper publish/delete and snapshots across local processes."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = data_dir / ".desktop-wallpaper.lock"
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(lock_path, flags, 0o600)
    with os.fdopen(descriptor, "r+b") as lock:
        os.fchmod(lock.fileno(), 0o600)
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _snapshot_db(source: Path, destination: Path) -> None:
    with sqlite3.connect(source) as src, sqlite3.connect(destination) as dst:
        src.backup(dst)
        mode = dst.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if mode.lower() != "delete":
            raise WallpaperBackupError("could not materialize desktop snapshot without WAL sidecars")
        if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise WallpaperBackupError("desktop database snapshot failed integrity_check")
    for suffix in ("-wal", "-shm"):
        if Path(str(destination) + suffix).exists():
            raise WallpaperBackupError(f"desktop snapshot left an unexpected sidecar: {suffix}")


def _wallpaper_rows(database: Path) -> list[tuple[str, str, str, int]]:
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise WallpaperBackupError("desktop database failed integrity_check")
            names = {row[1] for row in db.execute("PRAGMA table_info(desktop_wallpapers)")}
            if not names:
                return []
            required = {"owner", "asset_id", "media_type", "size"}
            if not required <= names:
                raise WallpaperBackupError("desktop wallpaper metadata schema is incomplete")
            return [
                (row["owner"], row["asset_id"], row["media_type"], row["size"])
                for row in db.execute(
                    "SELECT owner,asset_id,media_type,size FROM desktop_wallpapers ORDER BY owner"
                )
            ]
    except sqlite3.Error as exc:
        raise WallpaperBackupError(f"cannot inspect desktop database: {exc}") from exc


def _asset_file(root: Path, asset_id: str) -> Path:
    try:
        canonical = str(UUID(asset_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise WallpaperBackupError("invalid wallpaper asset ID") from exc
    if canonical != asset_id:
        raise WallpaperBackupError("non-canonical wallpaper asset ID")
    return root / "wallpapers" / f"{canonical}.webp"


def _verify_asset(path: Path, *, expected_size: int | None = None) -> None:
    if path.is_symlink() or not path.is_file():
        raise WallpaperBackupError(f"missing or unsafe wallpaper file: {path.name}")
    if expected_size is not None and path.stat().st_size != expected_size:
        raise WallpaperBackupError(f"wallpaper metadata size mismatch: {path.name}")
    try:
        with Image.open(path) as image:
            if image.format != "WEBP" or getattr(image, "n_frames", 1) != 1:
                raise WallpaperBackupError(f"invalid wallpaper encoding: {path.name}")
            image.verify()
    except (OSError, ValueError) as exc:
        raise WallpaperBackupError(f"invalid wallpaper image: {path.name}") from exc


def _validate_rows(root: Path, database: Path) -> list[dict]:
    wallpaper_dir = root / "wallpapers"
    if wallpaper_dir.is_symlink() or not wallpaper_dir.is_dir():
        raise WallpaperBackupError("wallpaper directory is missing or unsafe")
    records = []
    for owner, asset_id, media_type, size in _wallpaper_rows(database):
        if not owner or media_type != "image/webp" or not isinstance(size, int) or size < 0:
            raise WallpaperBackupError("invalid wallpaper metadata")
        asset = _asset_file(root, asset_id)
        _verify_asset(asset, expected_size=size)
        records.append({"owner": owner, "asset_id": asset_id, "media_type": media_type,
                        "size": size, "sha256": _sha(asset)})
    # A selected custom wallpaper must always resolve to a durable file.
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as db:
            for owner, definition in db.execute("SELECT owner,definition FROM preferences"):
                try:
                    prefs = json.loads(definition)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise WallpaperBackupError("invalid desktop preference JSON") from exc
                if prefs.get("wallpaper") == "custom" and not any(row["owner"] == owner for row in records):
                    raise WallpaperBackupError("custom preference has no wallpaper metadata")
    except sqlite3.Error as exc:
        raise WallpaperBackupError(f"cannot validate desktop preferences: {exc}") from exc
    return records


def create_desktop_wallpaper_backup(data_dir: Path, destination: Path) -> Path:
    """Create a new private snapshot while mutations are excluded by the shared data lock."""
    data_dir = _path_without_symlinks(Path(data_dir), strict=True)
    destination = _path_without_symlinks(Path(destination), strict=False)
    if destination == data_dir or destination.is_relative_to(data_dir) or data_dir.is_relative_to(destination):
        raise WallpaperBackupError("backup destination must be separate from the live desktop data root")
    if destination.exists() or destination.is_symlink():
        raise WallpaperBackupError("backup destination must not already exist")
    if (data_dir / "desktop.sqlite").is_symlink() or not (data_dir / "desktop.sqlite").is_file():
        raise WallpaperBackupError("desktop.sqlite is missing")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with wallpaper_data_lock(data_dir):
        staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
        try:
            os.chmod(staging, 0o700)
            _snapshot_db(data_dir / "desktop.sqlite", staging / "desktop.sqlite")
            (staging / "wallpapers").mkdir(mode=0o700)
            records = _validate_rows(data_dir, staging / "desktop.sqlite")
            for record in records:
                src = _asset_file(data_dir, record["asset_id"])
                dst = _asset_file(staging, record["asset_id"])
                shutil.copyfile(src, dst)
                os.chmod(dst, 0o600)
                _verify_asset(dst, expected_size=record["size"])
            manifest = {"format": "desktop-wallpaper-backup-v1", "wallpapers": records}
            raw = (json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n").encode()
            (staging / "manifest.json").write_bytes(raw)
            (staging / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
            os.chmod(staging / "desktop.sqlite", 0o600)
            os.chmod(staging / "manifest.json", 0o600)
            os.chmod(staging / "manifest.sha256", 0o600)
            verify_desktop_wallpaper_backup(staging)
            os.replace(staging, destination)
            return destination
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise


def verify_desktop_wallpaper_backup(backup: Path) -> dict:
    backup = _path_without_symlinks(Path(backup), strict=True)
    if not backup.is_dir():
        raise WallpaperBackupError("backup must be a regular directory")
    expected = {"desktop.sqlite", "wallpapers", "manifest.json", "manifest.sha256"}
    if {item.name for item in backup.iterdir()} != expected:
        raise WallpaperBackupError(f"backup has missing or unexpected entries: {sorted(item.name for item in backup.iterdir())}")
    for item in backup.iterdir():
        if item.is_symlink():
            raise WallpaperBackupError("backup contains symlinks")
    raw = (backup / "manifest.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != (backup / "manifest.sha256").read_text().strip():
        raise WallpaperBackupError("backup manifest checksum mismatch")
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WallpaperBackupError("invalid backup manifest") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != "desktop-wallpaper-backup-v1" or not isinstance(manifest.get("wallpapers"), list):
        raise WallpaperBackupError("unsupported desktop wallpaper backup format")
    db = backup / "desktop.sqlite"
    records = _validate_rows(backup, db)
    if manifest["wallpapers"] != records:
        raise WallpaperBackupError("backup wallpaper inventory does not match database/files")
    actual = {p.name for p in (backup / "wallpapers").iterdir()}
    listed = {f"{record['asset_id']}.webp" for record in records}
    if actual != listed or any((backup / "wallpapers" / name).is_symlink() for name in actual):
        raise WallpaperBackupError("backup contains missing, unsafe, or unlisted wallpaper files")
    return manifest


def restore_desktop_wallpaper_backup(backup: Path, destination: Path) -> Path:
    """Restore only desktop.sqlite and wallpapers to a new, absent data root."""
    backup = _path_without_symlinks(Path(backup), strict=True)
    destination = _path_without_symlinks(Path(destination), strict=False)
    if destination == backup or destination.is_relative_to(backup) or backup.is_relative_to(destination):
        raise WallpaperBackupError("restore destination must be separate from the backup")
    if destination.exists() or destination.is_symlink():
        raise WallpaperBackupError("restore destination must not already exist")
    verify_desktop_wallpaper_backup(backup)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        os.chmod(staging, 0o700)
        shutil.copyfile(backup / "desktop.sqlite", staging / "desktop.sqlite")
        os.chmod(staging / "desktop.sqlite", 0o600)
        shutil.copytree(backup / "wallpapers", staging / "wallpapers", copy_function=shutil.copyfile)
        os.chmod(staging / "wallpapers", 0o700)
        for path in (staging / "wallpapers").iterdir():
            os.chmod(path, 0o600)
        _validate_rows(staging, staging / "desktop.sqlite")
        os.replace(staging, destination)
        return destination
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
