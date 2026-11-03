#!/bin/sh
# Root-only wrapper: verified daily tar snapshot of the host's mounted data disk
# (issue #390). The data roots live on separate PVE data disks whose virtual size
# (200 GiB-8 TiB) makes VM-level vzdump impractical while the real content is
# small. Installed by the bootstrap stage.sh or the data-disk backup runbook
# operator step; hosts without a known data root exit 0 as a no-op.
set -eu

backup_root=${DATA_BACKUP_ROOT:-/var/backups/xeonolab/data}
keep=${DATA_BACKUP_KEEP:-7}
attempts=${DATA_BACKUP_ATTEMPTS:-2}
release=/opt/xeonolab/current

case "$keep$attempts" in *[!0-9]*) echo "invalid retention/attempt setting" >&2; exit 2;; esac

case "$(hostname 2>/dev/null || true)" in
    web-server) source=/srv/control ;;
    docker-server) source=/srv/docker ;;
    dev) source=/srv/dev ;;
    dev-agent) source=/srv/dev-agent ;;
    *)
        echo "no known data disk on this node"
        exit 0
        ;;
esac

if [ ! -d "$source" ]; then
    echo "data root is missing: $source"
    exit 0
fi
if [ ! -x "$release/.venv/bin/python" ] || [ ! -f "$release/scripts/data_backup_online.py" ]; then
    echo "active release is missing the data backup tool" >&2
    exit 2
fi

install -d -m 700 "$(dirname "$backup_root")"
install -d -m 700 "$backup_root"
exec "$release/.venv/bin/python" "$release/scripts/data_backup_online.py" snapshot \
    --source "$source" --root "$backup_root" --keep "$keep" --attempts "$attempts"
