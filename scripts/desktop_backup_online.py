#!/usr/bin/env python3
"""Create, verify, or prune a local VM101 desktop state snapshot (issue #390)."""

import argparse
import sys
from pathlib import Path

from xeonolab.desktop_backup import (
    DEFAULT_MAX_ACCOUNTS_BYTES,
    DEFAULT_MAX_ASSETS,
    DEFAULT_MAX_TOTAL_BYTES,
    DesktopBackupError,
    create_snapshot,
    prune_snapshots,
    verify_snapshot,
)
from xeonolab.wallpaper_backup import DEFAULT_FREE_SPACE_RESERVE_BYTES, WallpaperBackupError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    snapshot = commands.add_parser("snapshot")
    snapshot.add_argument("--data-dir", type=Path, required=True)
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--keep", type=int, default=7)
    snapshot.add_argument("--attempts", type=int, default=2)
    _add_budgets(snapshot, include_space=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--dir", dest="directory", type=Path, required=True)
    _add_budgets(verify, include_space=False)
    prune = commands.add_parser("prune")
    prune.add_argument("--root", type=Path, required=True)
    prune.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    try:
        if args.command == "snapshot":
            result = create_snapshot(
                args.data_dir, args.root, keep=args.keep, attempts=args.attempts,
                max_total_bytes=args.max_total_bytes,
                max_assets=args.max_assets,
                max_accounts_bytes=args.max_accounts_bytes,
                free_space_reserve_bytes=args.free_space_reserve_bytes,
            )
            print(f"snapshot ok: {result['snapshot']} (attempt {result['attempt']}, "
                  f"{result['accounts_bytes']} accounts bytes, {result['wallpapers']} wallpapers, "
                  f"{result['files']} files, pruned {len(result['pruned'])})")
        elif args.command == "verify":
            result = verify_snapshot(
                args.directory, max_total_bytes=args.max_total_bytes,
                max_assets=args.max_assets, max_accounts_bytes=args.max_accounts_bytes,
            )
            print(f"verified {args.directory}: {result['accounts_bytes']} accounts bytes, "
                  f"{result['wallpapers']} wallpapers, {result['files']} files")
        else:
            removed = prune_snapshots(args.root, args.keep)
            print(f"pruned {len(removed)} snapshot(s)")
    except (DesktopBackupError, WallpaperBackupError, OSError, ValueError) as error:
        print(f"desktop backup error: {error}", file=sys.stderr)
        return 2
    return 0


def _add_budgets(parser, *, include_space: bool) -> None:
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--max-assets", type=int, default=DEFAULT_MAX_ASSETS)
    parser.add_argument("--max-accounts-bytes", type=int, default=DEFAULT_MAX_ACCOUNTS_BYTES)
    if include_space:
        parser.add_argument(
            "--free-space-reserve-bytes", type=int, default=DEFAULT_FREE_SPACE_RESERVE_BYTES
        )


if __name__ == "__main__":
    raise SystemExit(main())
