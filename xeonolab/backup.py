"""Manifest-backed local XeoLibrary snapshots and isolated restore validation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from uuid import UUID


class BackupError(ValueError):
    pass


_HASH = re.compile(r"^[0-9a-f]{64}$")
_TABLE_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _db(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    db.row_factory = sqlite3.Row
    return db


def _integrity(path: Path) -> None:
    try:
        with _db(path) as db:
            result = db.execute("PRAGMA integrity_check").fetchone()[0]
            if result != "ok":
                raise BackupError(f"SQLite integrity failure: {path}: {result}")
    except sqlite3.Error as exc:
        raise BackupError(f"cannot read SQLite database {path}: {exc}") from exc


def _snapshot_sqlite(source: Path, destination: Path) -> None:
    source_db = _db(source)
    destination_db = sqlite3.connect(destination)
    try:
        source_db.backup(destination_db)
        mode = destination_db.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if mode.lower() != "delete":
            raise BackupError(f"could not materialize SQLite snapshot without WAL sidecars: {destination}")
    finally:
        source_db.close()
        destination_db.close()
    for suffix in ("-wal", "-shm"):
        if Path(str(destination) + suffix).exists():
            raise BackupError(f"SQLite snapshot left an unexpected sidecar: {destination}{suffix}")


def _regular(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise BackupError(f"expected regular file: {path}")


def _contains_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            return True
    return False


def validate_data_root(root: Path) -> list[dict]:
    root = root.resolve(strict=True)
    catalog = root / "catalogue.sqlite"
    _regular(catalog)
    _integrity(catalog)
    databases = {"catalogue.sqlite"}
    upload_db = root / "uploads.sqlite"
    if upload_db.exists():
        _regular(upload_db)
        _integrity(upload_db)
        databases.add("uploads.sqlite")
    _validate_sidecars(root, databases)
    try:
        with _db(catalog) as db:
            rows = db.execute("SELECT id,path,name FROM tables ORDER BY id").fetchall()
    except sqlite3.Error as exc:
        raise BackupError(f"invalid catalogue schema: {exc}") from exc
    table_dir = root / "tables"
    object_root = root / "objects" / "sha256"
    for p in (table_dir, object_root):
        if p.is_symlink() or not p.is_dir():
            raise BackupError(f"missing or unsafe directory: {p}")
    expected = set()
    referenced = set()
    records = []
    for row in rows:
        tid = row["id"]
        if not isinstance(tid, str) or not _TABLE_ID.fullmatch(tid) or tid in (".", ".."):
            raise BackupError(f"unsafe table id: {tid!r}")
        expected.add(f"{tid}.xl")
        path = table_dir / f"{tid}.xl"
        _regular(path)
        _integrity(path)
        try:
            with _db(path) as db:
                meta = db.execute("SELECT table_id,logical_path FROM __xl_meta").fetchone()
                if meta is None or meta["table_id"] != tid or meta["logical_path"] != row["path"]:
                    raise BackupError(f"catalogue/.xl mapping mismatch: {tid}")
                attachments = db.execute("SELECT object_hash,size FROM __xl_attachments").fetchall()
        except sqlite3.Error as exc:
            raise BackupError(f"invalid .xl schema {tid}: {exc}") from exc
        for att in attachments:
            digest = att["object_hash"]
            if not isinstance(digest, str) or not _HASH.fullmatch(digest):
                raise BackupError(f"invalid attachment hash in {tid}: {digest!r}")
            obj_rel = f"objects/sha256/{digest[:2]}/{digest[2:4]}/{digest}"
            obj = root / obj_rel
            _regular(obj)
            if _sha(obj) != digest or obj.stat().st_size != att["size"]:
                raise BackupError(f"attachment object hash/size mismatch: {obj_rel}")
            referenced.add(obj_rel)
        records.append({"id": tid, "path": row["path"], "name": row["name"], "file": f"tables/{tid}.xl"})
    actual = set()
    for p in table_dir.iterdir():
        if p.is_symlink() or not p.is_file():
            raise BackupError(f"unexpected table directory entry: {p}")
        if p.name.endswith((".xl-wal", ".xl-shm")):
            if p.name[:-4] not in expected:
                raise BackupError(f"orphan SQLite sidecar: {p}")
        elif p.suffix == ".xl":
            actual.add(p.name)
        else:
            raise BackupError(f"unexpected table directory entry: {p}")
    if expected != actual:
        raise BackupError(
            f"table file set mismatch; missing={sorted(expected - actual)}, orphan={sorted(actual - expected)}"
        )
    actual_objects = set()
    for p in object_root.rglob("*"):
        if p.is_symlink():
            raise BackupError(f"symlink in object store: {p}")
        if p.is_file():
            actual_objects.add(p.relative_to(root).as_posix())
        elif not p.is_dir():
            raise BackupError(f"unexpected object store entry: {p}")
    missing = referenced - actual_objects
    if missing:
        raise BackupError(f"missing attachment objects: {sorted(missing)}")
    for rel in actual_objects:
        parts = PurePosixPath(rel).parts
        if (
            len(parts) != 5
            or parts[:2] != ("objects", "sha256")
            or not _HASH.fullmatch(parts[4])
            or parts[2] != parts[4][:2]
            or parts[3] != parts[4][2:4]
        ):
            raise BackupError(f"invalid content-addressed object path: {rel}")
        if _sha(root / rel) != parts[4]:
            raise BackupError(f"content-addressed object hash mismatch: {rel}")
    return records


def _validate_sidecars(root: Path, databases: set[str]) -> None:
    for entry in root.iterdir():
        if entry.name.endswith(("-wal", "-shm")):
            database_name = entry.name[:-4]
            if database_name not in databases:
                raise BackupError(f"orphan SQLite sidecar: {entry}")
            _regular(entry)


def _object_manifest(root: Path) -> list[dict]:
    referenced = set()
    for table in (root / "tables").glob("*.xl"):
        with _db(table) as db:
            for row in db.execute("SELECT object_hash FROM __xl_attachments"):
                referenced.add(row["object_hash"])
    records = []
    for path in sorted((root / "objects/sha256").rglob("*")):
        if path.is_file():
            digest = path.name
            records.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": digest,
                    "size": path.stat().st_size,
                    "referenced": digest in referenced,
                }
            )
    return records


def _payload_files(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise BackupError(f"symlink in snapshot: {path}")
        if path.is_file():
            rel = path.relative_to(root).as_posix()
            result.append({"path": rel, "size": path.stat().st_size, "sha256": _sha(path)})
    return result


def create_backup(source: Path, destination: Path, *, quiesced: bool = False, online: bool = False) -> Path:
    """Snapshot a data root; either an externally enforced pause (quiesced) or a
    best-effort online copy whose manifest verification rejects a snapshot that
    raced a concurrent catalogue/file change (see scripts/xelibrary_backup_online.py)."""
    if not quiesced and not online:
        raise BackupError("production writes must be quiesced; pass explicit --quiesced acknowledgement")
    source = source.resolve(strict=True)
    validate_data_root(source)
    destination = destination.absolute()
    if destination.exists() or destination.is_symlink():
        raise BackupError(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.mkdir(mode=0o700)
    try:
        for rel in ["catalogue.sqlite", "uploads.sqlite", "tables", "objects"]:
            src, dst = source / rel, destination / rel
            if not src.exists():
                if rel == "uploads.sqlite":
                    continue
                raise BackupError(f"required source path is missing: {src}")
            if src.is_dir():
                dst.mkdir(mode=0o700)
                for p in sorted(src.rglob("*")):
                    sub = dst / p.relative_to(src)
                    if p.is_symlink():
                        raise BackupError(f"symlink in source: {p}")
                    if p.is_file() and p.name.endswith(("-wal", "-shm")):
                        # The SQLite backup API folds committed WAL frames into the snapshot.
                        continue
                    if p.is_dir():
                        sub.mkdir(mode=0o700, parents=True, exist_ok=True)
                    elif p.is_file():
                        if p.suffix == ".sqlite" or p.suffix == ".xl":
                            _snapshot_sqlite(p, sub)
                        else:
                            shutil.copyfile(p, sub)
                        os.chmod(sub, 0o600)
            else:
                _snapshot_sqlite(src, dst)
                os.chmod(dst, 0o600)
        tables = validate_data_root(destination)
        manifest = {
            "format": "xelibrary-backup-v1",
            "mode": "quiesced" if quiesced else "online",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_root": str(source),
            "tables": tables,
            "objects": _object_manifest(destination),
            "files": _payload_files(destination),
        }
        raw = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        (destination / "manifest.json").write_bytes(raw)
        os.chmod(destination / "manifest.json", 0o600)
        (destination / "manifest.sha256").write_text(hashlib.sha256(raw).hexdigest() + "\n")
        os.chmod(destination / "manifest.sha256", 0o600)
        os.chmod(destination, 0o700)
        verify_backup(destination)
        return destination
    except Exception:
        shutil.rmtree(destination)
        raise


def verify_backup(backup: Path) -> dict:
    backup = backup.resolve(strict=True)
    manifest_path = backup / "manifest.json"
    sidecar = backup / "manifest.sha256"
    _regular(manifest_path)
    _regular(sidecar)
    raw = manifest_path.read_bytes()
    if sidecar.read_text().strip() != hashlib.sha256(raw).hexdigest():
        raise BackupError("manifest checksum mismatch")
    try:
        manifest = json.loads(raw)
    except (ValueError, UnicodeError) as exc:
        raise BackupError(f"invalid manifest: {exc}") from exc
    if manifest.get("format") != "xelibrary-backup-v1" or not isinstance(manifest.get("files"), list):
        raise BackupError("unsupported manifest format")
    listed = set()
    for item in manifest["files"]:
        rel = item.get("path")
        pure = PurePosixPath(rel) if isinstance(rel, str) else None
        if pure is None or pure.is_absolute() or ".." in pure.parts or "\\" in rel or not pure.parts:
            raise BackupError(f"unsafe manifest path: {rel!r}")
        upload_path = False
        if len(pure.parts) == 3 and pure.parts[:2] == ("objects", ".uploads"):
            upload_path = re.fullmatch(r"\.[0-9a-f-]{36}\.[0-9a-f-]{36}\.tmp", pure.name) is not None
        elif len(pure.parts) == 4 and pure.parts[:3] == ("objects", ".uploads", ".locks"):
            try:
                lock_id = str(UUID(pure.name.removesuffix(".lock")))
            except ValueError:
                lock_id = ""
            upload_path = pure.name == f"{lock_id}.lock" and bool(lock_id)
        elif len(pure.parts) == 4 and pure.parts[:2] == ("objects", ".uploads"):
            try:
                session_valid = str(UUID(pure.parts[2])) == pure.parts[2]
            except ValueError:
                session_valid = False
            upload_path = session_valid and (
                re.fullmatch(r"[0-9]{8}\.part", pure.name) is not None
                or re.fullmatch(r"\.[0-9]{8}\.[0-9a-f-]{36}\.tmp", pure.name) is not None
            )
        allowed = (
            rel in ("catalogue.sqlite", "uploads.sqlite")
            or upload_path
            or (len(pure.parts) == 2 and pure.parts[0] == "tables" and pure.suffix == ".xl")
            or (
                len(pure.parts) == 5
                and pure.parts[:2] == ("objects", "sha256")
                and _HASH.fullmatch(pure.name)
                and pure.parts[2] == pure.name[:2]
                and pure.parts[3] == pure.name[2:4]
            )
        )
        if not allowed:
            raise BackupError(f"unexpected manifest payload path: {rel}")
        if rel in listed or rel in ("manifest.json", "manifest.sha256"):
            raise BackupError(f"duplicate/reserved manifest path: {rel}")
        listed.add(rel)
        path = backup.joinpath(*pure.parts)
        _regular(path)
        if path.stat().st_size != item.get("size") or _sha(path) != item.get("sha256"):
            raise BackupError(f"payload checksum mismatch: {rel}")
    actual = {p.relative_to(backup).as_posix() for p in backup.rglob("*") if p.is_file()}
    if any(p.is_symlink() for p in backup.rglob("*")) or actual != listed | {
        "manifest.json",
        "manifest.sha256",
    }:
        raise BackupError("backup contains unlisted files or symlinks")
    tables = validate_data_root(backup)
    if tables != manifest.get("tables"):
        raise BackupError("manifest table catalogue mismatch")
    if _object_manifest(backup) != manifest.get("objects"):
        raise BackupError("manifest object catalogue mismatch")
    return manifest


def restore_backup(backup: Path, restore_root: Path, name: str) -> Path:
    if not isinstance(name, str) or not _NAME.fullmatch(name) or name in (".", ".."):
        raise BackupError("restore name must be a safe single path component")
    backup = backup.resolve(strict=True)
    restore_root = restore_root.absolute()
    if _contains_symlink(restore_root) or not restore_root.is_dir():
        raise BackupError("restore root must be an existing real directory")
    restore_root = restore_root.resolve(strict=True)
    manifest = verify_backup(backup)
    target = restore_root / name
    original = Path(manifest["source_root"]).resolve()
    if target.exists() or target.is_symlink():
        raise BackupError(f"restore target already exists: {target}")
    if target == original or target in original.parents or original in target.parents:
        raise BackupError("restore target overlaps the original data root")
    if target == backup or target in backup.parents or backup in target.parents:
        raise BackupError("restore target overlaps the backup")
    target.mkdir(mode=0o700)
    try:
        # Empty directories are not represented by the file manifest, but both
        # are required by the data-root contract (including an empty library).
        (target / "tables").mkdir(mode=0o700)
        (target / "objects" / "sha256").mkdir(mode=0o700, parents=True)
        for item in manifest["files"]:
            rel = PurePosixPath(item["path"])
            out = target.joinpath(*rel.parts)
            out.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            shutil.copyfile(backup.joinpath(*rel.parts), out)
            os.chmod(out, 0o600)
        validate_data_root(target)
        return target
    except Exception:
        shutil.rmtree(target)
        raise
