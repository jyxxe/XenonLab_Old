# Release retention and disk hygiene on deployment targets

Every deployment stages an immutable release under `/opt/xeonolab/releases` on
each target (`101`, `104`, `106`, `107`, `111`). The active release is the
`/opt/xeonolab/current` symlink; previous releases are kept only for the
activation rollback window. They are never garbage-collected automatically, so
targets slowly fill up.

## Why this matters

On 2026-10-22 VM 107 reached **100% disk** (555 MiB free of 77 GiB, 133 release
directories totalling 73 GiB). The deployment of that day failed during staging
with `target 107 operation failed` before any Deploy Gateway record existed, so
the failure looked like a generic staging error. Pruning old releases while
keeping the current release and the three previous ones freed ~68 GiB and the
same deployment completed on the next attempt.

## Retention policy

- Keep the active release plus the **three newest previous releases**. That is
  the current activation's rollback window (`old` in
  `/opt/xeonolab/activations/<deployment-id>.json`) with two releases of margin.
- Never delete the release that `/opt/xeonolab/current` resolves to, even if it
  is older than the newest directories.
- Run pruning only from an operator session with `sudo -n` on the target, and
  dry-run first.
- Keep at least **5 GiB** free on `/` on every target; below **1 GiB** staging is
  expected to fail and a deployment must not be started.

## Procedure

1. Inspect one target:

   ```sh
   ssh -i <vm-key> xenon@192.168.0.<target> \
     'df -h /; readlink /opt/xeonolab/current; sudo -n ls /opt/xeonolab/releases | wc -l'
   ```

2. Dry-run the pruning tool from the active release checkout on the target (the
   script is part of the release tree, so copy it over or run it from a checkout
   mounted on the target):

   ```sh
   sudo -n python3 scripts/prune_releases.py --root /opt/xeonolab/releases \
     --current /opt/xeonolab/current --keep 3
   ```

   The plan lists the kept and deleted directories and never deletes anything.

3. Apply:

   ```sh
   sudo -n python3 scripts/prune_releases.py --apply
   ```

4. Verify the target afterwards:

   ```sh
   readlink /opt/xeonolab/current
   sudo -n ls /opt/xeonolab/releases | wc -l
   curl -sS -m 5 http://<target-address>:<port>/health/ready
   ```

   The symlink must be unchanged, four directories must remain, and every
   service readiness probe must return 200. Record the target, free space before
   and after, the number of removed directories and the checks in the
   deployment or PR evidence.

## Related operational hardening

- `infra/devagent/needrestart-transient.conf` must be installed at
  `/etc/needrestart/conf.d/devagent-transient.conf` on the DevAgent host so the
  needrestart APT hook never restarts `devagent-operation-*` or
  `devagent-preflight-*` transient units during a deploy.
- The LAN Caddy include (`/etc/caddy/xeonolab.caddy`) is operator-synced; see
  [`cloudflare-caddy-include.md`](cloudflare-caddy-include.md).
