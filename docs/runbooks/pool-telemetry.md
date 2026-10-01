# Physical pool telemetry deployment

The collector and receiver for plan.md §72 were installed on 2026-10-01; see
[production acceptance](../acceptance/pool-telemetry-2026-10-01.md). They collect one explicitly configured physical ZFS
pool on the PVE host and publishes its current byte counts to the HF-cache VM (VM 103 in the existing
inventory). The receiver writes the pool metrics file, and the HF-cache API is intended to read that same file
through its root-fed storage adapter; the receiver does not query PVE APIs or run commands on the PVE
host. Read-only PVE API inspection on 2026-10-01 confirmed the pool and host addresses below. The receiver deployment selects TCP 9183, verified unused on 2026-10-01.
The receiver/collector are enabled and validated; the HF-cache application consumer is not yet
installed on VM 103, so application storage/readiness acceptance remains pending.

## Confirmed production mapping

Read-only PVE inventory reports physical pool `aux-10t`, `ONLINE`, with `hf-cache-disk` backed by
`aux-10t/hf-cache` and `fnos-disk` backed by `aux-10t/fnos`. Both logical storages therefore share
the same physical pool and their displayed free space must never be added together. VM 103 (`hf-cache`)
is `192.168.0.103`; its 2 TiB data volume is already mounted as ext4 at `/srv/hf-cache`. The PVE host
is `192.168.0.10`.

The deployment values established by that read-only check are:

| Setting | Value |
|---|---|
| `POOL_TELEMETRY_POOL` | `aux-10t` |
| `POOL_TELEMETRY_ALLOWED_POOLS` | `aux-10t` |
| `POOL_TELEMETRY_BIND_ADDRESS` | `192.168.0.103` |
| `POOL_TELEMETRY_RECEIVER_URL` | `http://192.168.0.103:9183/api/v1/pool-metrics` |
| `POOL_METRICS_FILE` | `/var/lib/xeonolab/pool-telemetry/physical-pool.json` |
| HF-cache consumer file | `/var/lib/xeonolab/pool-telemetry/physical-pool.json` |
| `HF_CACHE_EXPECTED_POOL` | `aux-10t` |

TCP 9183 is the fixed receiver port, aligned with collector URL validation, systemd IP restrictions
and the deployment firewall. Changing this mapping requires reviewing all three together. Generate a new random HMAC secret and
install it independently on both hosts with root-only access; do not reuse any API credential.

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
| `POOL_TELEMETRY_RECEIVER_URL` | Required exact `http://192.168.0.103:9183/api/v1/pool-metrics`. | — |
| `POOL_TELEMETRY_BIND_ADDRESS` | — | Required VM 103 private interface address. |
| `POOL_TELEMETRY_PORT` | — | Required deployment TCP port `9183`. |
| `POOL_TELEMETRY_HMAC_SECRET` | Required shared random secret, at least 32 bytes. | Same secret, installed independently with root-only access. |
| `POOL_METRICS_FILE` | — | Required absolute path under `/var/lib/xeonolab/pool-telemetry/`, for example `physical-pool.json`. |
| `HF_CACHE_POOL_METRICS_FILE` | — | Required on the HF-cache API process; use the same absolute path as the receiver's `POOL_METRICS_FILE`. |
| `HF_CACHE_EXPECTED_POOL` | — | Required on the HF-cache API process; set to the exact physical pool name `aux-10t`. |
| `HF_CACHE_POOL_METRICS_MAX_AGE_SECONDS` | — | Recommended explicit value `120`; the consumer rejects older samples. |

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
only `zpool list` for the configured pool. Its IPAddressDeny/Allow restricts the receiver IP,
and the collector validates the fixed host and port before opening a connection. Memory/tasks are
bounded independently for the collector and receiver.

On VM 103, bind the receiver to its configured private address. Configure the VM and network firewalls
to permit inbound TCP only from the exact PVE host source address to the configured receiver port;
drop other sources to that port. On PVE, permit the collector's outbound connection only to the
configured VM 103 address and port. Do not expose the receiver through Caddy, the public ingress, or
any general application gateway. Keep normal management access rules separate. Verify the installed
rules from the firewall configuration; load `infra/hfcache/pool-telemetry-ingress.nft` on VM 103 and
`infra/pve/pool-telemetry-egress.nft` on PVE as `/etc/xeonolab/pool-telemetry.nft`.
Both use a dedicated `inet xeonolab_pool_telemetry` table; they do not flush unrelated tables.
Install `infra/network/xeonolab-pool-telemetry-firewall.service` on both hosts and verify
`nft -c -f` before activation. Collector/receiver require the firewall unit; reload it after changes.
Check first activation and repeated reload retain exactly the intended two rules. A rule accepting
a packet in this table does not override a later drop in another table; verify actual traffic.

Keep the `HF_CACHE_*` settings in the existing HF-cache API service configuration on VM 103, separate
from the receiver's environment file. The receiver's environment contains the HMAC secret; do not
load that file into the application process. Set `HF_CACHE_POOL_METRICS_FILE` to the same file path
as `POOL_METRICS_FILE`, set `HF_CACHE_EXPECTED_POOL=aux-10t`, and set the maximum sample age to 120
seconds. Use the service's existing configuration and deployment mechanism rather than inventing a
new application unit.

After installing the scripts and units, install the receiver and collector environment values,
reload systemd, and enable the receiver service and PVE timer. Confirm the receiver has written a
fresh root-owned metrics file before enabling the HF-cache consumer configuration. Then apply the
HF-cache environment settings and restart its existing service through the normal deployment
procedure. Verify that the HF-cache storage/readiness API reports the expected physical pool,
capacity, sample time, and watermark; verify a stale/missing sample fails closed using local tests,
not by corrupting production state. Check service status and inspect the metrics file locally; do
not treat this source change as production acceptance. Pool and address selection, secret
provisioning/rotation, firewall activation, and confirmation that the chosen pool is the physical
backing device all remain operator deployment work.
