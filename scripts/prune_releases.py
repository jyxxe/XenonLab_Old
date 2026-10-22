#!/usr/bin/python3
"""Prune old XeonoLab release directories on a deployment target.

Every deployment creates an immutable release directory under
``/opt/xeonolab/releases``. They are needed for rollback, but they accumulate:
on 2026-10-22 VM 107 reached 100% disk (555 MiB free of 77 GiB) and the next
deployment failed during staging, before any gateway record existed.

This tool keeps the active release plus the N newest previous releases (default
3, which covers the current activation's rollback window) and removes the rest.
It is a dry run unless ``--apply`` is passed, only ever deletes directories
directly under the release root, never follows symlinks and refuses to remove
the release the ``current`` symlink points at.

Usage on a target (root required for /opt/xeonolab):

    sudo python3 scripts/prune_releases.py                 # show the plan
    sudo python3 scripts/prune_releases.py --apply         # delete
    sudo python3 scripts/prune_releases.py --keep 5 --apply
"""

from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import sys

DEFAULT_ROOT = pathlib.Path("/opt/xeonolab/releases")
DEFAULT_CURRENT = pathlib.Path("/opt/xeonolab/current")


class PruneError(RuntimeError):
    """Unsafe layout or arguments; nothing is deleted."""


def _release_directories(root: pathlib.Path) -> list[pathlib.Path]:
    if not root.is_dir():
        raise PruneError(f"release root is not a directory: {root}")
    if root.is_symlink():
        raise PruneError(f"release root must not be a symlink: {root}")
    entries = []
    for entry in root.iterdir():
        if entry.is_symlink() or not entry.is_dir():
            continue
        if entry.parent != root:
            raise PruneError(f"unexpected release entry outside root: {entry}")
        entries.append(entry)
    entries.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    return entries


def plan_prune(
    root: pathlib.Path = DEFAULT_ROOT,
    current: pathlib.Path = DEFAULT_CURRENT,
    keep_previous: int = 3,
) -> dict:
    """Return the keep/delete plan without changing the filesystem.

    ``keep_previous`` counts previous releases kept in addition to the active
    one, so ``keep_previous=3`` keeps the current release and the three newest
    other releases.
    """
    if keep_previous < 0:
        raise PruneError("keep_previous must be zero or positive")
    entries = _release_directories(root)
    active = None
    if current.exists() or current.is_symlink():
        try:
            active = pathlib.Path(os.path.realpath(current))
        except OSError as error:  # pragma: no cover - realpath rarely raises
            raise PruneError(f"cannot resolve current release: {error}") from None
        if active.parent != pathlib.Path(os.path.realpath(root)):
            raise PruneError(f"current release is outside the release root: {active}")
    others = [entry for entry in entries if entry != active]
    keep = set(others[:keep_previous])
    if active is not None:
        keep.add(active)
    delete = [entry for entry in entries if entry not in keep]
    if active is not None and active in delete:  # pragma: no cover - defensive
        raise PruneError("refusing to delete the active release")
    return {"root": root, "active": active, "keep": sorted(keep), "delete": delete}


def apply_prune(plan: dict) -> int:
    root = plan["root"]
    active = plan.get("active")
    removed = 0
    for entry in plan["delete"]:
        if entry.parent != root or entry.is_symlink():
            raise PruneError(f"refusing to delete unsafe entry: {entry}")
        if active is not None and entry == active:
            raise PruneError("refusing to delete the active release")
        shutil.rmtree(entry)
        removed += 1
    return removed


def free_bytes(path: pathlib.Path = pathlib.Path("/")) -> int:
    stats = os.statvfs(path)
    return stats.f_bavail * stats.f_frsize


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prune old XeonoLab release directories.")
    parser.add_argument("--root", type=pathlib.Path, default=DEFAULT_ROOT)
    parser.add_argument("--current", type=pathlib.Path, default=DEFAULT_CURRENT)
    parser.add_argument(
        "--keep",
        type=int,
        default=3,
        help="previous releases to keep in addition to the active one (default 3)",
    )
    parser.add_argument("--apply", action="store_true", help="delete the planned directories")
    args = parser.parse_args(argv)
    try:
        plan = plan_prune(args.root, args.current, args.keep)
    except PruneError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    active = plan["active"]
    print(f"release root: {plan['root']}")
    print(f"active release: {active.name if active else '(no current symlink)'}")
    print(f"free before: {free_bytes() // (1024 * 1024)} MiB")
    print(f"keep ({len(plan['keep'])}): " + ", ".join(sorted(entry.name for entry in plan["keep"])))
    print(f"delete ({len(plan['delete'])}): " + ", ".join(sorted(entry.name for entry in plan["delete"])))
    if not args.apply:
        print("dry run: pass --apply to delete the listed directories")
        return 0
    removed = apply_prune(plan)
    print(f"removed {removed} release directories")
    print(f"free after: {free_bytes() // (1024 * 1024)} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
