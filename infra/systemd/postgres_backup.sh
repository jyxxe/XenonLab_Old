#!/bin/sh
# Root-only wrapper: verified nightly Keycloak PostgreSQL dump with local
# retention. Peer auth via `sudo -n -u postgres`; no credential is read here.
set -eu

root=${KEYCLOAK_BACKUP_ROOT:-/var/backups/xeonolab/keycloak}
keep=${KEYCLOAK_BACKUP_KEEP:-7}
database=${KEYCLOAK_BACKUP_DATABASE:-keycloak}
run_as=${KEYCLOAK_BACKUP_RUN_AS:-postgres}
tool=/usr/local/libexec/xeonolab-postgres-backup

case "$keep" in *[!0-9]*|'') echo "invalid KEYCLOAK_BACKUP_KEEP" >&2; exit 2;; esac

if [ ! -d /var/lib/postgresql ]; then
    echo "no PostgreSQL data root on this node"
    exit 0
fi
if [ ! -f "$tool" ]; then
    echo "missing backup tool: $tool" >&2
    exit 2
fi

install -d -m 700 "$(dirname "$root")"
install -d -m 700 "$root"
exec /usr/bin/python3 "$tool" snapshot --database "$database" --root "$root" \
    --keep "$keep" --run-as "$run_as"
