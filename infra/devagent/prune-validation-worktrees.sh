#!/bin/sh
# Remove stale transient validation worktrees so repeated preflights cannot fill
# the validation host's disk. Only directories named validate-* that are older
# than MAX_AGE_HOURS are touched; the active validation directory is too new.
set -eu
ROOT=${DEVAGENT_WORKTREE_ROOT:-/var/lib/devagent/worktrees}
MAX_AGE_HOURS=${DEVAGENT_WORKTREE_MAX_AGE_HOURS:-48}
if [ ! -d "$ROOT" ]; then
  echo "worktree root missing: $ROOT" >&2
  exit 0
fi
before=$(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name 'validate-*' | wc -l)
find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name 'validate-*' \
  -mmin "+$((MAX_AGE_HOURS * 60))" -print -exec rm -rf {} + > /dev/null
after=$(find "$ROOT" -mindepth 1 -maxdepth 1 -type d -name 'validate-*' | wc -l)
echo "validation worktrees: ${before} -> ${after} (max age ${MAX_AGE_HOURS}h)"
