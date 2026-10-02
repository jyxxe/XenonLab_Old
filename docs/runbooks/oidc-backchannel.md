# VM101 OIDC token-exchange backchannel

This runbook describes a root-operated VM101 Caddy vhost and a systemd mount
namespace change for `desktop-web`. The global `/etc/hosts` is not modified. It
keeps the browser issuer, callback, token URL, JWT checks, and TLS verification
unchanged. The internal route reduces VM101's dependency on an outbound public
round trip; it does not prove that round trip caused intermittent exchange
503s. Do not apply without independent review and the normal protected change
process. No repository script contains credentials or edits a production host.

## Route boundary

[`infra/network/oidc-backchannel.caddy`](../../infra/network/oidc-backchannel.caddy)
defines `https://xenon.jyxxe.com` on 443 with `tls internal`.
The fragment uses an explicit `route` block to preserve execution order: first
`respond @outside ... 403`, then `handle_path /auth/*`, then a fallback
`handle` returning 404. This is required because Caddyfile adaptation sorts
top-level directives; textual placement of a top-level `respond` before a
`handle` does not guarantee it runs first. Loopback requests under `/auth/*`
are stripped by `handle_path` and sent to `192.168.0.109:8180`, with fixed
`Host`, `X-Forwarded-Host`,
`X-Forwarded-Proto: https`, and `X-Forwarded-Prefix: /auth`. Other loopback
paths return 404. It contains no desktop, `/dev`, or generic reverse-proxy
handler. TLS verification in the desktop API remains enabled; VM101's existing
`OIDC_CA_FILE` must trust the Caddy internal CA used by `tls internal`.

The private hosts file is bind-mounted read-only only into the
`xeonolab@desktop-web.service` mount namespace. VM101's host resolver and other
services keep the public DNS route. Public-path probes from the host or other
services therefore continue to use Cloudflare; a request made inside the
desktop-web namespace exercises the local vhost. Remote desktop browser
resolution is unchanged.

## Apply procedure

An authorized operator performs these steps on VM101 as root. Do not print
secret-bearing files or environment values into logs.

1. Confirm the change window, current release, Caddy health, and a working
   rollback path. Inspect the Caddy service's configured user/group and data
   directories with `systemctl show caddy -p User -p Group -p ExecStart` and
   the unit's non-secret XDG storage settings. Confirm the local CA file named
   by `OIDC_CA_FILE` is available to the desktop service and is the root used
   by Caddy's internal PKI. Do not read or copy the desktop env file to output.
2. Confirm `hosts:` in `/etc/nsswitch.conf` checks `files` before DNS/NSS
   network sources. Check that global `/etc/hosts` has no mapping for
   `xenon.jyxxe.com`, and that the host resolver currently returns public
   addresses. Create a root-only backup directory (mode `0700`) and
   preserve the current `/etc/caddy/Caddyfile`, any existing
   `/etc/caddy/oidc-backchannel.caddy`, and any existing
   `/etc/xeonolab/oidc-backchannel.hosts` or desktop-web systemd drop-in,
   including ownership and modes. Record hashes and metadata without dumping
   contents. Do not change global `/etc/hosts`.
3. Install the fragment at `/etc/caddy/oidc-backchannel.caddy`, root-owned and
   readable by the Caddy service group. Prepare a candidate Caddyfile that
   adds exactly one explicit `import /etc/caddy/oidc-backchannel.caddy` while
   preserving every current site and the existing Cloudflare include. Keep
   the live Caddyfile untouched until validation succeeds. Make the candidate
   readable by the Caddy service user and preserve the live Caddyfile's owner,
   group, and mode when installing the validated candidate.
4. Validate the candidate as the Caddy service user, with the same XDG data
   and config directories as the running service, for example:

   ```sh
   sudo -u caddy env XDG_DATA_HOME=/var/lib/caddy/.local/share \
     XDG_CONFIG_HOME=/var/lib/caddy/.config \
     /usr/bin/caddy validate --config /etc/caddy/Caddyfile.candidate \
     --adapter caddyfile
   ```

   Substitute the actual service user and storage directories established in
   step 1; do not assume the example paths. Stop on any warning/error that
   prevents validation. Then atomically install the candidate Caddyfile,
   reload Caddy, and confirm the service is active. Keep the backup available.
5. Before changing the desktop-web namespace, verify the local listener and route using
   explicit resolution and the LAN CA (this validates TLS, hostname, vhost,
   route, and discovery without changing name resolution):

   ```sh
   curl --fail --silent --show-error \
     --resolve xenon.jyxxe.com:443:127.0.0.1 \
     --cacert /etc/xeonolab/lan-root-ca.crt \
     https://xenon.jyxxe.com/auth/realms/xeonolab/.well-known/openid-configuration
   ```

   Check the JSON issuer is exactly
   `https://xenon.jyxxe.com/auth/realms/xeonolab`, and authorization, token,
   and JWKS URLs remain HTTPS on `xenon.jyxxe.com` under `/auth/realms/xeonolab`.
   Confirm a loopback request to another path returns 404. From a separate
   LAN host, force the hostname to VM101's LAN address and confirm the vhost
   itself returns HTTP 403. For example, use curl with
   `--resolve xenon.jyxxe.com:443:<VM101-LAN-IP>` and the same trusted LAN CA,
   requesting `/auth/realms/xeonolab/.well-known/openid-configuration`. The
   response must be 403, not Keycloak discovery JSON. A timeout, connection
   refusal, firewall rejection, or other network-level denial is not evidence
   that Caddy's route guard works; do not proceed until a separate LAN client
   reaches Caddy and observes 403. Repeat after reload and before any desktop
   namespace change. Do not proceed unless TLS, issuer, path, loopback 404,
   and actual remote 403 checks pass.
6. Build `/etc/xeonolab/oidc-backchannel.hosts` by preserving the current
   `/etc/hosts` contents and adding exactly `127.0.0.1 xenon.jyxxe.com`. Confirm
   no conflicting mapping, then install root:root mode `0644`. Install a
   root-owned mode `0644` drop-in at
   `/etc/systemd/system/xeonolab@desktop-web.service.d/oidc-backchannel.conf`
   containing exactly:

   ```ini
   [Service]
   BindReadOnlyPaths=/etc/xeonolab/oidc-backchannel.hosts:/etc/hosts
   ```

   Prepare the hosts file in a temporary file in `/etc/xeonolab`, copy the
   current `/etc/hosts`, append the one mapping, set owner/mode, and rename it
   into place on the same filesystem. Prepare the drop-in in a temporary file
   in its destination directory and rename it into place as well. Do not
   replace `/etc/hosts` itself.

   Run `systemctl daemon-reload`, then restart only
   `xeonolab@desktop-web.service` (this briefly interrupts desktop API/auth
   requests). Confirm it is active and healthy. Obtain its
   `MainPID`; in that process's mount namespace verify `getent ahostsv4
   xenon.jyxxe.com` resolves to `127.0.0.1` and `/etc/hosts` is the read-only
   bind mount (for example with `nsenter -t <MainPID> -m -- getent ahostsv4
   xenon.jyxxe.com` and `findmnt -N <MainPID> -T /etc/hosts`, if supported).
   On the host, verify `getent ahostsv4 xenon.jyxxe.com` still returns public
   addresses and not loopback. From within the service mount namespace, repeat
   TLS/discovery against the hostname without `--resolve` using the existing
   LAN CA, for example:

   ```sh
   nsenter -t <MainPID> -m -- curl --fail --silent --show-error \
     --cacert /etc/xeonolab/lan-root-ca.crt \
     https://xenon.jyxxe.com/auth/realms/xeonolab/.well-known/openid-configuration
   ```

   Then use the normal authorized exchange verification and check its
   result without exposing codes or tokens. No other VM101 service should
   resolve the hostname to loopback.

## Rollback

If validation or Caddy reload fails, restore the backed-up Caddyfile and
fragment state, validate with the same service user/data directories, reload
Caddy, and verify prior local site health. If any failure occurs after the
systemd change, restore only the saved desktop-web drop-in and private hosts
file state, run `systemctl daemon-reload`, and restart only
`xeonolab@desktop-web.service`; this removes the private resolver override.
Verify inside the service namespace it returns to public DNS, while host and
other service resolution remain unchanged throughout. Then restore the Caddy
file/fragment if needed, validate, reload, and confirm the previous LAN site
and public OIDC discovery from an external machine. If an unrelated change
modified a file since backup, stop and reconcile manually instead of
overwriting it. Record rollback through the approved operations process.

The resolver override is limited to the desktop-web service mount namespace.
It does not change VM101 host resolution, other services/VMs, Cloudflare,
Keycloak issuer policy, browser frame policy, or DNS. If these boundaries
cannot be confirmed, do not apply this procedure.

## Failed remote-denial check and rollback record — 2026-10-02

The first candidate passed Caddy validation, loopback TLS/discovery, and the
loopback 404 check. A separate LAN client then received HTTP 200 with public-name
Keycloak discovery instead of 403. This exposed that the top-level `respond`
guard was sorted after `handle` routes by Caddyfile adaptation; the earlier
local checks had not proven remote isolation. The candidate was rolled back
before installing the desktop-web hosts file or systemd mount drop-in. The
operator record is `/var/backups/xeonolab/oidc-backchannel-20261002/operation.json`
on the host; its final state is `rolled_back`. The revised fragment uses an
explicit ordered `route` block. It is not eligible for re-application until a
fresh Caddy 2.11.6 listener test and a separate-LAN request demonstrate an
actual 403, alongside loopback TLS/discovery and 404 checks.
