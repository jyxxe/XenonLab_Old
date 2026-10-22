#!/usr/bin/env python3
"""Create, verify, or restore a local desktop database + wallpaper snapshot."""

import argparse
from pathlib import Path

from xeonolab.wallpaper_backup import (
    DEFAULT_FREE_SPACE_RESERVE_BYTES,
    DEFAULT_MAX_ASSETS,
    DEFAULT_MAX_TOTAL_BYTES,
    create_desktop_wallpaper_backup,
    restore_desktop_wallpaper_backup,
    verify_desktop_wallpaper_backup,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("backup")
    backup.add_argument("--data-dir", type=Path, required=True)
    backup.add_argument("--destination", type=Path, required=True)
    _add_budgets(backup, include_space=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    _add_budgets(verify, include_space=False)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    _add_budgets(restore, include_space=True)
    args = parser.parse_args()
    if args.command == "backup":
        result = create_desktop_wallpaper_backup(
            args.data_dir, args.destination,
            max_total_bytes=args.max_total_bytes,
            max_assets=args.max_assets,
            free_space_reserve_bytes=args.free_space_reserve_bytes,
        )
        print(f"desktop wallpaper backup created: {result}")
    elif args.command == "verify":
        manifest = verify_desktop_wallpaper_backup(
            args.backup, max_total_bytes=args.max_total_bytes, max_assets=args.max_assets
        )
        print(f"desktop wallpaper backup verified: {len(manifest['wallpapers'])} wallpapers")
    else:
        result = restore_desktop_wallpaper_backup(
            args.backup, args.destination,
            max_total_bytes=args.max_total_bytes,
            max_assets=args.max_assets,
            free_space_reserve_bytes=args.free_space_reserve_bytes,
        )
        print(f"desktop wallpaper data restored: {result}")
    return 0


def _add_budgets(parser, *, include_space: bool) -> None:
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--max-assets", type=int, default=DEFAULT_MAX_ASSETS)
    if include_space:
        parser.add_argument(
            "--free-space-reserve-bytes", type=int, default=DEFAULT_FREE_SPACE_RESERVE_BYTES
        )


if __name__ == "__main__":
    raise SystemExit(main())
