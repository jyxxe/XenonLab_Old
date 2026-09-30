#!/bin/sh
set -eu
# Binary and upstream checksum must already be downloaded to /tmp.
expected=$(awk '{print $1}' /tmp/forgejo.sha256)
printf '%s  /tmp/forgejo\n' "$expected" | sha256sum -c -
getent passwd forgejo >/dev/null || useradd --system --home /var/lib/forgejo --create-home --shell /bin/bash forgejo
install -m 755 /tmp/forgejo /usr/local/bin/forgejo
install -d -m 750 -o forgejo -g forgejo /var/lib/forgejo /var/lib/forgejo/data /var/lib/forgejo/log
install -d -m 750 -o root -g forgejo /etc/forgejo
if [ ! -f /etc/forgejo/app.ini ]; then
 cat >/etc/forgejo/app.ini <<'INI'
APP_NAME = XeonoLab · Development
RUN_USER = forgejo
RUN_MODE = prod
WORK_PATH = /var/lib/forgejo
[database]
DB_TYPE = sqlite3
PATH = /var/lib/forgejo/data/forgejo.db
[repository]
ROOT = /var/lib/forgejo/repositories
[server]
DOMAIN = 192.168.0.101
ROOT_URL = https://192.168.0.101:9445/
HTTP_ADDR = 192.168.0.110
HTTP_PORT = 3000
DISABLE_SSH = true
LFS_START_SERVER = false
[security]
INSTALL_LOCK = true
[service]
DISABLE_REGISTRATION = true
REQUIRE_SIGNIN_VIEW = false
[log]
MODE = console
LEVEL = Info
INI
 chown forgejo:forgejo /etc/forgejo/app.ini
 chmod 640 /etc/forgejo/app.ini
fi
install -m 644 /tmp/forgejo.service /etc/systemd/system/forgejo.service
systemctl daemon-reload
systemctl enable --now forgejo
