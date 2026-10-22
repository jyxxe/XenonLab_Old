# ADR 0007 — Public OIDC issuer over a VM-local TLS backchannel

The desktop browser continues using the canonical public issuer
`https://xenon.jyxxe.com/auth/realms/xeonolab`. Desktop-web on VM101 exchanges
authorization codes and refresh tokens server-side, and must keep the token
endpoint on the exact configured issuer host, port, and realm path. Do not
change the issuer, callback, token URL, TLS verification, or issuer validation
to address a network-path failure.

When VM101's desktop-web process needs to reach that public token URL, its
systemd mount namespace receives a private read-only `/etc/hosts` file mapping
`xenon.jyxxe.com` to `127.0.0.1`. A dedicated Caddy vhost terminates TLS with
the existing LAN-trusted Caddy internal CA, accepts only loopback clients, and
proxies only `/auth/*` to Keycloak on VM109. It fixes the forwarded host, HTTPS
scheme, and `/auth` prefix so Keycloak continues to advertise the unchanged
public issuer. Other paths return 404 to loopback; all non-loopback clients are
denied. This is an internal transport route for one host, not a new public site
or issuer.

The allowlist is enforced with an explicit Caddy `route` block. Its ordered
handlers first return 403 for every non-loopback request, then handle only
`/auth/*`, and finally return 404 for other loopback paths. Do not rely on
textual order between top-level `respond` and `handle` directives: Caddyfile
adaptation sorts directives, and a top-level guard can run after a handler.
An actual request from a separate LAN client must receive HTTP 403 from this
vhost before the desktop-web namespace is changed; a network-level rejection
does not prove the route guard.

The mapping is mounted only into desktop-web's systemd namespace; VM101's host
and other services retain public DNS resolution. Verify both views after
installation. No other VM, system DNS, Cloudflare route, firewall, or desktop
browser name resolution is changed.

Apply and rollback are operator-controlled, backed-up changes described in
[`docs/runbooks/oidc-backchannel.md`](../runbooks/oidc-backchannel.md). The
change reduces dependence on VM101's outbound public/Tunnel round trip; it does
not establish that the public round trip caused any observed 503. Keep the
public issuer and existing strict endpoint checks until separate evidence and
review justify another change.
