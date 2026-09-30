#!/usr/bin/env python3
"""Explicitly downgrade the XeLibrary catalogue ACL table to the legacy schema."""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

LEGACY_COLUMNS = ("resource_type", "resource_id", "grants")
CURRENT_COLUMNS = (*LEGACY_COLUMNS, "revision", "explicit")


class RollbackError(RuntimeError):
    """The catalogue cannot be safely downgraded."""


def _columns(connection):
    return tuple(row[1] for row in connection.execute("PRAGMA table_info(acl)"))


def _check_integrity(path):
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        raise RollbackError(f"cannot open/verify SQLite backup {path}: {exc}") from exc
    if result != ("ok",):
        raise RollbackError(f"backup failed SQLite integrity_check: {result}")


def _backup_database(source: Path, backup: Path):
    if source.resolve() == backup.resolve():
        raise RollbackError("backup path must differ from the catalogue")
    if backup.exists():
        raise RollbackError(f"refusing to overwrite existing backup: {backup}")
    backup.parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(source) as original, sqlite3.connect(backup) as destination:
            original.backup(destination)
        _check_integrity(backup)
    except Exception:
        backup.unlink(missing_ok=True)
        raise


def rollback_acl_database(catalogue: Path, backup: Path, *, service_stopped: bool):
    """Back up then atomically downgrade ACL; the caller must confirm writes are stopped."""
    catalogue, backup = Path(catalogue), Path(backup)
    if not service_stopped:
        raise RollbackError("stop XeLibrary writes and pass service_stopped=True before rollback")
    if not catalogue.is_file():
        raise RollbackError(f"catalogue does not exist: {catalogue}")

    _backup_database(catalogue, backup)
    try:
        connection = sqlite3.connect(catalogue, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN IMMEDIATE")
            columns = _columns(connection)
            if columns != CURRENT_COLUMNS:
                raise RollbackError(
                    f"expected ACL columns {CURRENT_COLUMNS}, found {columns}; no downgrade performed"
                )

            records = connection.execute(
                "SELECT resource_type,resource_id,grants FROM acl WHERE explicit=1 "
                "ORDER BY resource_type,resource_id"
            ).fetchall()
            # Validate all retained grants before changing the live schema.
            for row in records:
                try:
                    grants = json.loads(row["grants"])
                except (TypeError, json.JSONDecodeError) as exc:
                    raise RollbackError(
                        f"invalid grants JSON for {row['resource_type']}:{row['resource_id']}"
                    ) from exc
                if not isinstance(grants, dict) or any(
                    not isinstance(principal, str)
                    or not isinstance(scopes, list)
                    or any(not isinstance(scope, str) for scope in scopes)
                    for principal, scopes in grants.items()
                ):
                    raise RollbackError(
                        f"invalid grants for {row['resource_type']}:{row['resource_id']}"
                    )

            connection.execute(
                "CREATE TABLE acl_legacy(resource_type TEXT, resource_id TEXT, grants TEXT, "
                "PRIMARY KEY(resource_type,resource_id))"
            )
            connection.executemany(
                "INSERT INTO acl_legacy(resource_type,resource_id,grants) VALUES(?,?,?)",
                [tuple(row) for row in records],
            )
            connection.execute("DROP TABLE acl")
            connection.execute("ALTER TABLE acl_legacy RENAME TO acl")
            connection.execute("COMMIT")
        except Exception:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        finally:
            connection.close()
    except (sqlite3.Error, OSError) as exc:
        raise RollbackError(f"ACL downgrade failed; transaction rolled back: {exc}") from exc
    return {"retained_explicit_grants": len(records), "backup": str(backup)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("catalogue", type=Path, help="path to catalogue.sqlite")
    parser.add_argument("--backup", required=True, type=Path, help="new, non-existing safety-backup path")
    parser.add_argument(
        "--service-stopped",
        action="store_true",
        help="confirm XeLibrary is stopped or otherwise prevented from writing",
    )
    args = parser.parse_args(argv)
    try:
        result = rollback_acl_database(args.catalogue, args.backup, service_stopped=args.service_stopped)
    except RollbackError as exc:
        print(f"ACL rollback refused: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"state": "completed", **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
