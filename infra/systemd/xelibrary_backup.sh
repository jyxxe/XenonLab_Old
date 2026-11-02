#!/bin/sh
# Root-only wrapper: create and retain a verified online XeLibrary snapshot.
# The snapshot lives on the same disk as the source (see runbook: not offsite).
set -eu

source_root=${XELIBRARY_BACKUP_SOURCE:-/srv/xelibrary/core/xeonolab}
backup_root=${XELIBRARY_BACKUP_ROOT:-/var/backups/xeonolab/xelibrary}
keep=${XELIBRARY_BACKUP_KEEP:-7}
attempts=${XELIBRARY_BACKUP_ATTEMPTS:-3}
release=/opt/xeonolab/current

case "$keep$attempts" in *[!0-9]*) echo "invalid retention/attempt setting" >&2; exit 2;; esac

if [ ! -d "$source_root" ]; then
    # Most targets do not host XeLibrary data; the timer is a no-op there.
    echo "no XeLibrary data root on this node: $source_root"
    exit 0
fi
if [ ! -x "$release/.venv/bin/python" ] || [ ! -f "$release/scripts/xelibrary_backup_online.py" ]; then
    echo "active release is missing the backup tool" >&2
    exit 2
fi

install -d -m 700 "$(dirname "$backup_root")"
install -d -m 700 "$backup_root"
exec "$release/.venv/bin/python" "$release/scripts/xelibrary_backup_online.py" snapshot \
    --source "$source_root" --root "$backup_root" --keep "$keep" --attempts "$attempts"
