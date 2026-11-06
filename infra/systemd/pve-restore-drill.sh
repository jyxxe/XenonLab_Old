#!/bin/bash
# Monthly isolated PVE restore drill using the newest scheduled vzdump archive
# (issue #113). Runs as the devagent user from xeonolab-pve-restore-drill.service:
# restores into scratch VMID 190 with the NIC isolated, verifies the guest agent,
# then destroys the clone. Failures are real alarms, so the unit exits non-zero.
set -u

release=/opt/xeonolab/current
token_file=/var/lib/devagent/.config/devagent/pve-api-token
log_dir=/var/lib/devagent/logs
log=$log_dir/pve-restore-drill.jsonl
max_bytes=2097152

if [ ! -x "$release/.venv/bin/python" ] || [ ! -f "$release/scripts/pve_restore_drill.py" ]; then
    echo "active release is missing the restore drill tool" >&2
    exit 2
fi
if [ ! -f "$token_file" ]; then
    echo "PVE API token file is missing: $token_file" >&2
    exit 2
fi

install -d -m 755 "$log_dir"
if [ -f "$log" ] && [ "$(stat -c %s "$log")" -gt "$max_bytes" ]; then
    mv -f "$log" "$log.1"
fi

"$release/.venv/bin/python" "$release/scripts/pve_restore_drill.py" \
    --latest-archive-for "${XEL_RESTORE_SOURCE_VMID:-106}" \
    --vmid "${XEL_RESTORE_SCRATCH_VMID:-190}" \
    --storage "${XEL_RESTORE_STORAGE:-local-lvm}" \
    --source-vmid "${XEL_RESTORE_SOURCE_VMID:-106}" \
    --token-file "$token_file" --insecure --execute \
    >>"$log" 2>>"$log_dir/pve-restore-drill.err"
status=$?
echo "pve-restore-drill exit=$status at $(date -u +%Y-%m-%dT%H:%M:%SZ)" >&2
exit "$status"
