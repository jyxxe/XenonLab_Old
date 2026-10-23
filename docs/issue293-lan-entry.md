# Issue #293 — explicit LAN entry and `.lan` retirement status

## Shipped: visible LAN entry in the Xenon Panel

The System app's 网络 (network) panel now shows an explicit 内网直连入口:
`https://192.168.0.101:9443` (override with `VITE_LAN_ORIGIN` at build time), a
copy button, and a hint that the device must trust the LAN CA and that the LAN
and public origins use different OIDC issuers, so switching entries may require a
fresh login. When the page is already served from that IP origin the panel says
so instead.

This is deliberately explicit rather than a silent speed switch: a public page
cannot probe or switch to the LAN origin on its own because

- the LAN edge uses Caddy's internal CA, so an untrusted device fails TLS before
  any probe can succeed;
- cross-origin calls to that origin would need CORS plus credentials;
- Cloudflare Tunnel traffic reaches the origin from the tunnel connector, so the
  server cannot infer the client's network position.

## `.lan` retirement — staged, not yet removed

Stage 1 (this change and its documentation): stop advertising `.lan` hostnames.
The README now directs new configurations to IP origins, and the LAN entry is
IP-based. The Caddy `.lan` virtual hosts and the `.lan` OIDC origin→issuer map are
kept for compatibility because they still have real dependencies:

| Dependency | Why removal is staged |
|---|---|
| `deploy.xenon.lan` in the protected deploy executor / `execute_release.py` | production deployments call that URL |
| OIDC origin→issuer map for `xenon.lan` / `auth.xenon.lan` | existing LAN sessions and redirects |
| `tools.xenon.lan`, `llm.xenon.lan`, `library.xenon.lan`, `chat.xenon.lan` | independent service origins |
| verification scripts and tests | fixtures use `xenon.lan` as a synthetic origin |

Remaining stages: add IP-based access for the services that still need a browser
origin, migrate the deploy executor and verification scripts to the IP endpoint,
then remove the `.lan` site addresses and issuer mappings with a dedicated
verification pass (LAN login, deploy, service health). Each stage needs its own
release and operator sync of the LAN Caddy include, so it should be tracked
separately from this increment.

## Validation

`apps/desktop-web/tests/lan-entry.test.mjs` covers origin normalisation, the
state/hint copy for public vs LAN origins and clipboard success/failure. The
desktop shell acceptance opens System → 网络 and asserts the IP entry, the copy
button and the CA caveat. The panel is read-only and writes nothing.
