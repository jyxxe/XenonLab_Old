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

## Automatic retention (issue #383)

Every target now runs a systemd timer that applies the same policy once a day
(04:30 local, persistent across downtime) so release directories cannot grow
unbounded between manual audits:

- `infra/systemd/release_prune.sh` → `/usr/local/libexec/xeonolab-release-prune`:
  root-only wrapper that executes the **active release's**
  `scripts/prune_releases.py --keep 3 --apply`. The reviewed tool owns the
  safety checks (active release, symlinks, release-root containment), so the
  timer cannot delete anything the manual procedure would not.
- `infra/systemd/xeonolab-release-prune.service` + `.timer`: oneshot service,
  `Nice=10`/idle IO, `ConditionPathExists` on the current release tool.
- `infra/systemd/stage.sh` installs and enables the timer during bootstrap when
  the unit files are supplied in `/tmp`.

Roll out or refresh it on a target (operator session):

```sh
sudo -n install -m 755 /path/to/release_prune.sh /usr/local/libexec/xeonolab-release-prune
sudo -n install -m 644 /path/to/xeonolab-release-prune.service /etc/systemd/system/
sudo -n install -m 644 /path/to/xeonolab-release-prune.timer /etc/systemd/system/
sudo -n systemctl daemon-reload
sudo -n systemctl enable --now xeonolab-release-prune.timer
sudo -n systemctl start xeonolab-release-prune.service   # verify once now
sudo -n journalctl -u xeonolab-release-prune.service -n 20 --no-pager
sudo -n systemctl list-timers xeonolab-release-prune.timer --no-pager
```

Expected: journal shows `keep (4)` / `delete (N)` / `removed N`, the release
count stays at four (current + three previous), `current` is unchanged and
`/health/ready` still returns 200. The timer never restarts application
services; a pruning failure only logs and leaves the rest of the node intact.

## Procedure (manual fallback)


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
