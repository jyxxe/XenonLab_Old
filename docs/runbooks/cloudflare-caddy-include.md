# Web 101 Cloudflare Caddy include release and rollback

This procedure keeps the public Tunnel routes in the fixed Web 101 include
`/etc/caddy/cloudflare-origin.caddy` under the same staged release and Deploy
Gateway approval as the application. It does not replace `/etc/caddy/Caddyfile`
or any other local site/import. Web 101 uses its existing root-managed
`reload_caddy` activation policy. `scripts/stage_release.py` does not install
or change the root activation helper, its module, or activation policy.

The activation helper reads only
`<release>/infra/network/cloudflare-origin.caddy`. It substitutes that
candidate for the exact `/etc/caddy/cloudflare-origin.caddy` import in a
temporary copy of the live Caddyfile and runs `/usr/bin/caddy validate` against
the complete config. Validation must succeed before the include is atomically
replaced. The replacement keeps the current file's owner, group, and mode. A
validation or copy failure does not reload Caddy. A changed include is reloaded
only after the application health checks pass. Before changing services or
files, the activation helper flushes and fsyncs the snapshot record, atomically
replaces its state file, and fsyncs the activation directory. Each record
stores the previous include bytes and metadata under
`/opt/xeonolab/activations/<deployment-id>.json`; the record is root-managed
and supports the existing fixed `rollback <deployment-id>` receiver command.

## Required control-plane rollout boundary

The current installed forced-command receiver only knows its fixed
`activate <commit> <deployment-id>` and `rollback <deployment-id>` operations;
it cannot install a new activation helper from an application release. The
Issue #69 helper therefore must be installed as a separately approved
control-plane update before an application release can use Caddy include sync.
That update must install the reviewed `infra/systemd/activate.py` and
`infra/systemd/caddy_sync.py` at their existing fixed root-owned paths and
confirm Web 101's existing activation policy has `reload_caddy: true`. Do this
through the authorized management/bootstrap procedure and its own review and
approval. Do not add an activation command, shell payload, or root-file copy to
the Deploy Gateway executor to work around the boundary. Without that update,
the currently installed receiver will activate application releases without
synchronizing this include.

## Staging and activation

1. Complete the separate approved control-plane update above. Confirm the
   installed helper and module match the reviewed commit and the existing
   Web 101 activation policy enables Caddy reload.
2. From the approved feature commit, build the normal release with
   `scripts/stage_release.py`. The stage operation uses the existing immutable
   release path and does not replace the include, activation helper, module, or
   activation policy, and does not reload Caddy.
3. Before activation, have the operator confirm `/etc/caddy/Caddyfile` contains
   exactly one `import /etc/caddy/cloudflare-origin.caddy` line. Preserve the
   current Caddyfile, Cloudflare include, and file metadata in an operator
   controlled recovery location. Do not print secret-bearing configuration.
4. Activate through the normal Deploy Gateway release approval and lease.
   Review the deployment result. Web 101 validates the complete Caddy config,
   checks application health, then installs and reloads the changed include.
   The remaining fixed inventory targets continue through the same executor.
5. Confirm the deployment state is `completed`, the Caddy service is active,
   and the public desktop and `/auth/*` path work through the Tunnel. Check the
   forwarded prefix and host/proto behavior using the approved operator
   acceptance procedure. Keep the deployment id with the release record.

## Failure and rollback

- If Caddy validation fails, activation leaves the live include untouched and
  does not reload Caddy. The activation helper restores the previous application
  symlink and services; the fixed executor rolls back targets already activated.
  Inspect the Caddy validation error through the operator's normal deployment
  logs, correct the release, and stage it again before retrying.
- If Caddy reload fails after the include was installed, Web 101 restores the
  saved include bytes and metadata, validates the restored configuration, and
  attempts a recovery reload before reporting activation failure. The fixed
  executor then rolls back the other activated targets.
- For an approved post-deployment rollback, use the existing Deploy Gateway
  rollback action with that deployment id. Web 101 validates and restores the
  recorded include snapshot, preserves its saved owner/group/mode, and reloads
  Caddy only if restoration changed the file. The executor restores the prior
  application release across its fixed target inventory.
- If the recorded activation state is unavailable or its automatic rollback
  was not confirmed, keep the deployment frozen as required by ADR 0002. An
  authorized operator can restore the protected Caddyfile/include copies and
  previous release through the established management process; validate the
  complete Caddyfile before reloading. Do not use an ad hoc Deploy Gateway
  command or broaden its fixed executor allowlist.

No production staging, Caddy validation, reload, or login acceptance was
performed for this implementation. The operator must verify the live Caddy
binary/path, exact import line, file ownership/mode, and Tunnel behavior before
applying it. The control-plane update is a prerequisite and does not occur
through normal release staging.
