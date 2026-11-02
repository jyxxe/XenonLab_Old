#!/bin/sh
set -eu
commit="$1"
node="$2"
case "$commit" in *[!a-f0-9]*|'') exit 2;; esac
[ ${#commit} -eq 40 ] || exit 2
getent passwd xeonolab >/dev/null || useradd --system --home /var/lib/xeonolab --create-home --shell /usr/sbin/nologin xeonolab
getent passwd xeonolab-deploy >/dev/null || useradd --system --home /var/lib/xeonolab-deploy --create-home --shell /bin/sh xeonolab-deploy
install -d -m 755 /opt/xeonolab/releases
install -d -m 750 -o root -g xeonolab /etc/xeonolab
install -d -m 750 -o xeonolab -g xeonolab /var/lib/xeonolab
install -d -m 700 -o xeonolab-deploy -g xeonolab-deploy /var/lib/xeonolab-deploy/.ssh
install -m 600 -o xeonolab-deploy -g xeonolab-deploy /tmp/xeonolab-config/authorized_keys /var/lib/xeonolab-deploy/.ssh/authorized_keys
# System accounts start locked on some distributions; enable SSH key authentication without a usable password.
usermod -p '*' xeonolab-deploy
install -d -m 755 /usr/local/libexec
install -m 755 /tmp/activate.py /usr/local/libexec/xeonolab-activate
install -m 644 /tmp/caddy_sync.py /usr/local/libexec/caddy_sync.py
install -m 755 /tmp/receiver.py /usr/local/libexec/xeonolab-receiver
printf 'xeonolab-deploy ALL=(root) NOPASSWD: /usr/local/libexec/xeonolab-activate\n' >/etc/sudoers.d/xeonolab-deploy
chmod 440 /etc/sudoers.d/xeonolab-deploy
visudo -cf /etc/sudoers.d/xeonolab-deploy >/dev/null
for file in /tmp/xeonolab-config/*; do
 case "$file" in */authorized_keys) continue;; esac
 install -m 640 -o root -g xeonolab "$file" /etc/xeonolab/
done
install -m 644 /tmp/xeonolab@.service /etc/systemd/system/xeonolab@.service
install -m 755 /tmp/xeonolab-uv /usr/local/bin/uv
install -d -m 755 /opt/xeonolab/python
UV_PYTHON_INSTALL_DIR=/opt/xeonolab/python uv python install 3.12
install -d -m 755 "/opt/xeonolab/releases/$commit"
tar -xf /tmp/xeonolab-release.tar -C "/opt/xeonolab/releases/$commit"
cd "/opt/xeonolab/releases/$commit"
if [ "$node" = 107 ]; then
 UV_PYTHON_INSTALL_DIR=/opt/xeonolab/python uv sync --locked --no-dev --extra inference --python 3.12
 chown -R root:xeonolab /opt/xeonolab/D100M-Embedding
 chmod -R g+rX /opt/xeonolab/D100M-Embedding
else
 UV_PYTHON_INSTALL_DIR=/opt/xeonolab/python uv sync --locked --no-dev --python 3.12
fi
if [ "$node" = 104 ]; then
 install -d -m 750 -o xeonolab -g xeonolab /srv/xelibrary/core/xeonolab
 install -m 644 /tmp/d100m-tokenizer.json /opt/xeonolab/d100m-tokenizer.json
fi
# Explicit internal DNS mapping; no router/DHCP changes.
if ! grep -q '# xeonolab-lan' /etc/hosts; then
 printf '\n192.168.0.101 xenon.lan auth.xenon.lan library.xenon.lan tools.xenon.lan deploy.xenon.lan llm.xenon.lan # xeonolab-lan\n' >> /etc/hosts
fi
systemctl daemon-reload
# Release retention (issue #383): keep /opt/xeonolab/releases bounded so targets
# cannot slowly fill up with every deployment again. Installed only when the
# bootstrap supplies the reviewed unit files; existing targets are rolled out
# by the disk-retention runbook and re-verified on every deployment.
# Daily verified XeLibrary snapshot (issue #390): installed only when the
# bootstrap supplies the reviewed wrapper/units; otherwise the timer is rolled
# out by the backup runbook on the data-holding target.
if [ -f /tmp/xelibrary_backup.sh ] && [ -f /tmp/xeonolab-xelibrary-backup.service ] && [ -f /tmp/xeonolab-xelibrary-backup.timer ]; then
    install -m 755 /tmp/xelibrary_backup.sh /usr/local/libexec/xeonolab-xelibrary-backup
    install -m 644 /tmp/xeonolab-xelibrary-backup.service /etc/systemd/system/xeonolab-xelibrary-backup.service
    install -m 644 /tmp/xeonolab-xelibrary-backup.timer /etc/systemd/system/xeonolab-xelibrary-backup.timer
    systemctl daemon-reload
    systemctl enable --now xeonolab-xelibrary-backup.timer
fi
if [ -f /tmp/release_prune.sh ] && [ -f /tmp/xeonolab-release-prune.service ] && [ -f /tmp/xeonolab-release-prune.timer ]; then
    install -m 755 /tmp/release_prune.sh /usr/local/libexec/xeonolab-release-prune
    install -m 644 /tmp/xeonolab-release-prune.service /etc/systemd/system/xeonolab-release-prune.service
    install -m 644 /tmp/xeonolab-release-prune.timer /etc/systemd/system/xeonolab-release-prune.timer
    systemctl daemon-reload
    systemctl enable --now xeonolab-release-prune.timer
fi
rm -rf /tmp/xeonolab-config
printf 'staged VM %s release %s\n' "$node" "$commit"
