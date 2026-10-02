#!/usr/bin/env python3
"""Create, verify, or restore a local desktop database + wallpaper snapshot."""

import argparse
from pathlib import Path

from xeonolab.wallpaper_backup import (
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
    verify = commands.add_parser("verify")
    verify.add_argument("--backup", type=Path, required=True)
    restore = commands.add_parser("restore")
    restore.add_argument("--backup", type=Path, required=True)
    restore.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "backup":
        result = create_desktop_wallpaper_backup(args.data_dir, args.destination)
        print(f"desktop wallpaper backup created: {result}")
    elif args.command == "verify":
        manifest = verify_desktop_wallpaper_backup(args.backup)
        print(f"desktop wallpaper backup verified: {len(manifest['wallpapers'])} wallpapers")
    else:
        result = restore_desktop_wallpaper_backup(args.backup, args.destination)
        print(f"desktop wallpaper data restored: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
