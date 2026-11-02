#!/bin/sh
# Root-only wrapper: verified nightly Forgejo snapshot with local retention.
# VM110 is not a XeonoLab release target, so this wrapper and the python tool
# are installed from the repo by the Forgejo backup runbook (operator step).
set -eu

source_dir=${FORGEJO_BACKUP_SOURCE:-/var/lib/forgejo}
config=${FORGEJO_BACKUP_CONFIG:-/etc/forgejo/app.ini}
backup_root=${FORGEJO_BACKUP_ROOT:-/var/backups/xeonolab/forgejo}
keep=${FORGEJO_BACKUP_KEEP:-7}
service=${FORGEJO_BACKUP_SERVICE:-forgejo}
tool=/usr/local/libexec/xeonolab-forgejo-backup

case "$keep" in *[!0-9]*|'') echo "invalid FORGEJO_BACKUP_KEEP" >&2; exit 2;; esac

if [ ! -d "$source_dir" ]; then
    echo "no Forgejo data root on this node: $source_dir"
    exit 0
fi
if [ ! -f "$tool" ]; then
    echo "missing backup tool: $tool" >&2
    exit 2
fi

install -d -m 700 "$(dirname "$backup_root")"
install -d -m 700 "$backup_root"
exec /usr/bin/python3 "$tool" snapshot --source "$source_dir" --config "$config" \
    --root "$backup_root" --keep "$keep" --service "$service"
