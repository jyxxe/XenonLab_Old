#!/bin/sh
# Root-only helper: keep /opt/xeonolab/releases bounded.
#
# Runs the prune tool that ships inside the active release, so the retention
# policy lives with the release validation tests. The tool itself refuses to
# touch the active release, symlinks or anything outside the release root.
set -eu

current=/opt/xeonolab/current
script="$current/scripts/prune_releases.py"
keep="${XEONOLAB_RELEASE_KEEP:-3}"

case "$keep" in *[!0-9]*|'') echo "invalid XEONOLAB_RELEASE_KEEP: $keep" >&2; exit 2;; esac
[ -n "$keep" ] || exit 2

if [ ! -f "$script" ]; then
    # No current release yet (fresh target): nothing to retain.
    echo "no active release, no release pruning needed"
    exit 0
fi

echo "release pruning: current=$current keep=$keep"
exec /usr/bin/python3 "$script" --keep "$keep" --apply
