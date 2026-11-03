#!/usr/bin/env python3
"""Create, verify, or prune a verified tar snapshot of a mounted data disk (issue #390)."""

import argparse
import sys
from pathlib import Path

from xeonolab.data_backup import (
    DataBackupError,
    create_snapshot,
    prune_snapshots,
    verify_snapshot,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--source", type=Path, required=True)
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--keep", type=int, default=7)
    snapshot.add_argument("--attempts", type=int, default=2)
    verify = commands.add_parser("verify")
    verify.add_argument("--dir", dest="directory", type=Path, required=True)
    prune = commands.add_parser("prune")
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = create_snapshot(args.source, args.root, keep=args.keep, attempts=args.attempts)
            print(f"snapshot ok: {result['snapshot']} (attempt {result['attempt']}, "
                  f"{result['size']} bytes, {result['entries']} entries, "
                  f"skipped {result['skipped_special']} special, pruned {len(result['pruned'])})")
        elif args.command == "verify":
            result = verify_snapshot(args.directory)
            print(f"verified {args.directory}: {result['size']} bytes, "
                  f"{result['entries']} entries, skipped {result['skipped_special']} special")
        else:
            removed = prune_snapshots(args.root, args.keep)
            print(f"pruned {len(removed)} snapshot(s)")
    except (DataBackupError, OSError, ValueError) as error:
        print(f"data backup error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
