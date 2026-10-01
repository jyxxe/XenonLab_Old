#!/bin/sh
# Operator-only infrastructure bootstrap. The runner stays stopped until the
# repository registration file and reviewed configuration are installed.
set -eu

test "$(id -u)" -eq 0
test -f /tmp/xeonolab-forgejo-runner
test -f /tmp/xeonolab-forgejo-runner.sha256
cd /tmp
# v13.2.0 verified against the Forgejo release signing key
# EB114F5E6C0DC2BCDD183550A4B61A2DC5923710 by the operator before transfer.
expected_sha256=fadaec897f5e6641c363f87ecaf75f866f99c81b7097ec6b433e4c48123fcad1
actual_sha256=$(sha256sum xeonolab-forgejo-runner | cut -d ' ' -f 1)
test "$actual_sha256" = "$expected_sha256"
sha256sum --check xeonolab-forgejo-runner.sha256

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y podman uidmap git ca-certificates
if ! getent passwd forgejo-runner >/dev/null; then
    useradd --create-home --home-dir /var/lib/forgejo-runner \
        --shell /usr/sbin/nologin forgejo-runner
fi
chmod 0700 /var/lib/forgejo-runner
install -m 0755 /tmp/xeonolab-forgejo-runner /usr/local/bin/forgejo-runner
install -d -m 0750 -o root -g forgejo-runner /etc/forgejo-runner
loginctl enable-linger forgejo-runner
systemctl --user -M forgejo-runner@ enable --now podman.socket

runner_uid=$(id -u forgejo-runner)
cat > /etc/forgejo-runner/environment <<EOF
DOCKER_HOST=unix:///run/user/$runner_uid/podman/podman.sock
XDG_RUNTIME_DIR=/run/user/$runner_uid
EOF
chmod 0640 /etc/forgejo-runner/environment
chown root:forgejo-runner /etc/forgejo-runner/environment
rm -f /tmp/xeonolab-forgejo-runner /tmp/xeonolab-forgejo-runner.sha256
echo 'Rootless Podman ready; install repository registration/config before starting runner.'
