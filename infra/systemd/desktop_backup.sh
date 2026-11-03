#!/bin/sh
# Root-only wrapper: verified nightly desktop state snapshot (accounts.sqlite,
# desktop.sqlite and its wallpaper assets) with local retention. Issue #390.
# Installed by the bootstrap stage.sh or by the desktop backup runbook operator
# step (protected deploys only update the release tree); other targets have no
# desktop data root and the timer is a no-op there.
set -eu

data_dir=${DESKTOP_BACKUP_DATA_DIR:-/var/lib/xeonolab/desktop-web}
backup_root=${DESKTOP_BACKUP_ROOT:-/var/backups/xeonolab/desktop}
keep=${DESKTOP_BACKUP_KEEP:-7}
attempts=${DESKTOP_BACKUP_ATTEMPTS:-2}
release=/opt/xeonolab/current

case "$keep$attempts" in *[!0-9]*) echo "invalid retention/attempt setting" >&2; exit 2;; esac

if [ ! -f "$data_dir/accounts.sqlite" ]; then
    echo "no desktop data root on this node: $data_dir"
    exit 0
fi
if [ ! -x "$release/.venv/bin/python" ] || [ ! -f "$release/scripts/desktop_backup_online.py" ]; then
    echo "active release is missing the desktop backup tool" >&2
    exit 2
fi

install -d -m 700 "$(dirname "$backup_root")"
install -d -m 700 "$backup_root"
exec "$release/.venv/bin/python" "$release/scripts/desktop_backup_online.py" snapshot \
    --data-dir "$data_dir" --root "$backup_root" --keep "$keep" --attempts "$attempts"
