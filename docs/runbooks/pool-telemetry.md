# Physical pool telemetry deployment

This is the local implementation for plan.md §72. It collects one explicitly configured physical ZFS
pool on the PVE host and publishes its current byte counts to the HF-cache VM (VM 103 in the existing
inventory). The receiver writes the HF-cache metrics file; it does not query PVE APIs or run commands
on the PVE host. No pool name, host address, receiver port, or HMAC key is selected by this change.

## File contract

The receiver accepts one JSON object with exactly these fields:

```json
{"pool":"CONFIGURED_POOL","total":1000,"used":250,"free":750,"observed_at":"2026-10-01T00:00:00Z"}
```

`pool` must match the receiver's explicit `POOL_TELEMETRY_ALLOWED_POOLS` allowlist. The integers are
non-negative byte counts; `total` must be positive and `used + free == total`. `observed_at` is UTC
with whole-second precision. The authenticated timestamp must be current and no more than 120 seconds
old, and it must be newer than the sample already on disk. The body is limited to 4096 bytes.

The receiver atomically replaces the configured JSON file, fsyncs the file and parent directory, and
sets owner/mode to root:root/0644. The directory and any existing file must be root-owned and not
group/world writable. A malformed existing file is preserved and causes the receiver to fail closed;
it cannot reset replay protection. Invalid, expired, and replayed requests leave the last file intact.
The consumer must check `observed_at` age itself and treat samples older than 120 seconds as stale,
including after a receiver restart.

## Required deployment configuration

Set these values in `/etc/xeonolab/pool-telemetry.env`, owned by root and mode 0600, on both hosts.
Keep all values deployment-specific; do not copy a sample pool name or secret from source control.

| Variable | PVE collector | VM 103 receiver |
|---|---|---|
| `POOL_TELEMETRY_POOL` | Required exact physical pool name selected by the operator. No implicit default. | — |
| `POOL_TELEMETRY_ALLOWED_POOLS` | — | Required comma-separated expected pool allowlist; include only pool names intentionally reported to HF-cache. |
| `POOL_TELEMETRY_RECEIVER_URL` | Required `http://<VM103-private-address>:<port>/api/v1/pool-metrics`. | — |
| `POOL_TELEMETRY_BIND_ADDRESS` | — | Required VM 103 private interface address. |
| `POOL_TELEMETRY_PORT` | — | Required TCP port, 1–65535. |
| `POOL_TELEMETRY_HMAC_SECRET` | Required shared random secret, at least 32 bytes. | Same secret, installed independently with root-only access. |
| `POOL_METRICS_FILE` | — | Required absolute path under `/var/lib/xeonolab/pool-telemetry/`, for example `physical-pool.json`. |

The timestamp and HMAC signature cover the HTTP method, fixed endpoint path, timestamp header, and
exact request body. The receiver never logs request bodies or headers. The collector's only subprocess
is the fixed argv `/sbin/zpool list -H -p -o name,size,alloc,free <configured-pool>` with shell use
disabled. It performs no PVE API calls. TLS is not provided by this narrow receiver; keep this HTTP
endpoint on the management/application network and enforce the source restriction below. Do not route
it over an untrusted network. The receiver applies a five-second socket read timeout and limits itself
to eight concurrent request handlers; excess connections are closed so slow clients cannot accumulate
unbounded threads.

## Least-privilege installation and network policy

Install `infra/pve/pool_telemetry_collector.py` as
`/usr/local/libexec/xeonolab-pool-telemetry-collector` on PVE and install the PVE `.service` and
`.timer` units under `/etc/systemd/system/`. Install `infra/hfcache/pool_metrics_receiver.py` as
`/usr/local/libexec/xeonolab-pool-telemetry-receiver` and its unit on VM 103. Install Python 3 and
create the configured root-owned state directory with mode 0755 on VM 103. Keep each service's
environment file root-owned mode 0600; never put a secret in unit contents, command-line arguments,
or logs. Both scripts use only Python standard-library modules.

The receiver runs as root only so it can publish the required root-owned file. Its systemd unit has a
read-only system view and a single writable state directory, no capabilities, no device access, no
home access, and no privilege gain. The collector runs as root for read-only `/dev/zfs` access; its
unit grants only `CAP_SYS_ADMIN`, exposes only `/dev/zfs`, and has no writable system paths. It invokes
only `zpool list` for the configured pool.

On VM 103, bind the receiver to its configured private address. Configure the VM and network firewalls
to permit inbound TCP only from the exact PVE host source address to the configured receiver port;
drop other sources to that port. On PVE, permit the collector's outbound connection only to the
configured VM 103 address and port. Do not expose the receiver through Caddy, the public ingress, or
any general application gateway. Keep normal management access rules separate. Verify the installed
rules from the firewall configuration; this change does not install or activate production rules.

After installing the scripts and units, install the required environment values, reload systemd, and
enable the receiver service and PVE timer. Check service status and inspect the metrics file locally;
do not treat this source change as production acceptance. Pool and address selection, secret
provisioning/rotation, firewall activation, and confirmation that the chosen pool is the physical
backing device all remain operator deployment work.
