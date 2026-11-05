#!/bin/bash
# Append one public/LAN entry sample round to a JSONL log (issue #126).
# Runs as the devagent user from xeonolab-public-probe.service; failures are
# data, not service failures: the unit exits 0 as long as the probe ran and the
# report was written. Exit 2 only when the active release lacks the tool.
set -u

release=/opt/xeonolab/current
log_dir=/var/lib/devagent/logs
log=$log_dir/public-probe.jsonl
max_bytes=5242880

if [ ! -x "$release/.venv/bin/python" ] || [ ! -f "$release/scripts/public_probe.py" ]; then
    echo "active release is missing the public probe tool" >&2
    exit 2
fi

install -d -m 755 "$log_dir"
if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt "$max_bytes" ]; then
    mv -f "$log" "$log.1"
fi

"$release/.venv/bin/python" "$release/scripts/public_probe.py" \
    --url https://xenon.jyxxe.com/ \
    --url https://192.168.0.101/ \
    --url https://chat.jyxxe.com/ \
    --url https://chat.xenon.jyxxe.com/ \
    --ca-file /etc/devagent/lan-root-ca.crt \
    --samples 6 --interval 2 --json >>"$log" 2>>"$log_dir/public-probe.log"
status=$?
echo "public-probe exit=$status at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
exit 0
