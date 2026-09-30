#!/usr/bin/env python3
"""Create, verify, and restore a local XeoLibrary backup snapshot."""

import argparse
import sys
from pathlib import Path

from xeonolab.backup import BackupError, create_backup, restore_backup, verify_backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    create = subs.add_parser("backup")
    create.add_argument("--source", type=Path, required=True)
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument(
        "--quiesced", action="store_true", help="acknowledge an externally enforced write pause"
    )
    verify = subs.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    restore = subs.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--restore-root", type=Path, required=True)
    restore.add_argument("--name", required=True, help="new directory name under restore-root")
    args = parser.parse_args()
    try:
        if args.command == "backup":
            result = create_backup(args.source, args.destination, quiesced=args.quiesced)
        elif args.command == "verify":
            result = verify_backup(args.backup)
        else:
            result = restore_backup(args.backup, args.restore_root, args.name)
        print(result if isinstance(result, Path) else f"verified {len(result['files'])} files")
        return 0
    except (BackupError, OSError) as exc:
        print(f"backup error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
