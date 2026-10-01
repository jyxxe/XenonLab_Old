# Physical pool telemetry production acceptance — 2026-10-01

Issue #66 / Plan §72, infrastructure source `d565c0c`. The coordinator installed
the reviewed collector on PVE and receiver on VM 103; no application release was
activated by this infrastructure bootstrap. HF-cache application acceptance is
still pending.

## Physical source and deployment

PVE `zpool list -H -p` confirms `aux-10t` is healthy, with total
9,998,683,865,088 bytes, used 25,280,512 and free 9,998,658,584,576. Its
`fnos/vm-102-disk-0` is an 8 TiB volume and `hf-cache/vm-103-disk-0` a 2 TiB
volume; these share the same physical pool. VM 103 mounts `/dev/sdb` ext4 at
`/srv/hf-cache`.

Receiver: `192.168.0.103:9183`, enabled and running, root-owned metrics file
`/var/lib/xeonolab/pool-telemetry/physical-pool.json` mode 0644. Both hosts keep
the independently generated HMAC in root:root mode 0600 environment files.
PVE's 30-second timer is enabled; successful oneshot collection reports
`Result=success`, `ExecMainStatus=0`. Collector and receiver memory peaks were
approximately 10 and 13 MiB. No secret was added to Git or reports.

## Actual checks

- Both hosts passed `nft -c`; installed units passed `systemd-analyze verify`
  (VM 103 emitted unrelated legacy `CPUAccounting` warnings).
- First activation succeeded; two subsequent firewall reloads on each host
  retained two rules in the dedicated table without changing the table-name
  set. Existing management access and PVE firewall services remained available.
- Requests from VM 111 and NetworkServer VM 105 were refused by the receiver's
  source restriction. PVE's accepted traffic advanced both firewall counters.
- From the authorized PVE source, invalid HMAC, signed expired sample and signed
  replay each returned 401. The metrics file remained unchanged.
- During a short collector-timer pause, receiver restart preserved the accepted
  sample and replay protection. The timer was resumed in a `finally` cleanup;
  a new sample advanced from `09:02:58Z` to `09:03:12Z`, with byte values matching
  PVE. Subsequent scheduled refresh was also checked for advancement and age
  below 120 seconds.
- The collector validates the fixed receiver IP **and port** before opening any
  connection. Independent review passed; 68 telemetry/HF-cache tests and all
  290 backend tests passed. Changed-file Ruff, OpenAPI export (no diff) and
  `git diff --check` passed.

Private evidence is under `runtime/verification/issue-66/`, including
`operational-checks.json`, installed-service status, rule snapshots and byte
inventory. These checks do not simulate a physical pool filling up or a host
reboot.

## Remaining acceptance

VM 103 has no HF-cache application unit or `/opt/xeonolab` installation yet.
The API consumer must be onboarded through the normal release process with
`HF_CACHE_POOL_METRICS_FILE`, `HF_CACHE_EXPECTED_POOL=aux-10t` and maximum age
120 seconds. Actual application readiness, stale/missing telemetry rejecting
new downloads while verified cache hits remain readable, Hub transfer and
watermark-driven eviction are not proven by this receiver acceptance. Issues
#66 and #22 remain open.

To stop this bootstrap, stop the PVE timer/collector and VM 103 receiver first.
Only then disable the dedicated firewall unit and remove its own table if
retiring the endpoint; preserve unrelated tables and management services.
