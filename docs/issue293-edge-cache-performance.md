# Issue #293 — Xenon Panel intranet load speed and `.lan` retirement

## Measured baseline (LAN edge, 2026-10-22)

| Item | Value |
|---|---|
| `GET /` entry | 354 B, 7–8 ms round trip through Caddy |
| Hashed JavaScript bundle | 590,880 B raw, ~193 KB with `zstd` |
| Hashed CSS bundle | 18 KB with `zstd` |
| `Cache-Control` on `/assets/*` | **absent** before this change |
| `Cache-Control` on `/` | absent (served by the SPA file handler) |

The LAN round trip is already single-digit milliseconds, so the repeated cost is
not latency but transfer: without a cache policy the browser cannot treat the
content-hashed bundle as immutable and may re-download or revalidate roughly
0.2 MB of compressed JavaScript plus the CSS on navigation.

## Shipped in this increment

Both edges (`infra/network/xeonolab.caddy`, `infra/network/cloudflare-origin.caddy`)
now declare:

```caddyfile
@immutable_assets path /assets/*
header @immutable_assets Cache-Control "public, max-age=31536000, immutable"
@spa_entry path / /index.html
header @spa_entry Cache-Control "no-cache"
```

Content-hashed Vite assets are cached for a year (safe: a new release emits new
filenames), while the SPA entry is always revalidated so a release is picked up on
the next load. API responses keep their own `Cache-Control` (for example
`/api/v1/auth/config` already sends `no-store`). The contract test
`tests/test_edge_static_cache_policy.py` keeps this policy from drifting, and the
existing edge security-header contract is unchanged.

Verification after deployment: the LAN edge must return the immutable header for
`/assets/*`, `no-cache` for `/` and `/index.html`, and the public edge must match
after activation installs `cloudflare-origin.caddy`.

## Remaining asks in #293

### 1. "When browsing via the public domain from the same intranet, load some resources directly from the intranet"

A browser cannot silently switch to the LAN origin:

- The LAN edge uses Caddy's internal CA. Unless the client device trusts that CA,
  `https://192.168.0.101:9443` fails TLS and no fetch/probe can succeed.
- Cross-origin requests to the LAN origin would need CORS plus credentials, and
  the public page cannot know it is on the same L2/L3 segment from within the
  browser sandbox.

Practical options, in order of effort:

1. **Explicit LAN entry** (recommended for now): keep the public desktop as the
   single URL, and add a visible "内网入口" link that points at
   `https://192.168.0.101:9443/` for devices that installed the LAN CA. This is
   deterministic, requires no probing and no mixed-origin credentials.
2. **Detect at login**: after a successful login the server knows the session, but
   not the client's network position, because Cloudflare tunnel traffic reaches
   the origin from the tunnel connector. A server-side hint would therefore be a
   heuristic at best.
3. **LAN-CA provisioning**: ship the LAN root CA to managed devices (MDM/manual
   install) and then a LAN probe could be attempted safely; this is a client
   provisioning project, not a desktop code change.

### 2. "Retire all `.lan` access; use IP addresses only"

Current `.lan` dependencies were inventoried; they are not just bookmarks:

| Dependency | Impact if `.lan` is removed without migration |
|---|---|
| `deploy.xenon.lan` in the protected deploy executor (`infra/devagent/deploy_runner.py`, `scripts/execute_release.py`) | **Breaks production deployments**, because the executor calls `https://deploy.xenon.lan` |
| `xenon.lan` / `auth.xenon.lan` OIDC origin→issuer map (`infra/systemd/update_oidc_maps.py`, browser-session code and tests) | Existing `.lan` sessions and LAN login redirects stop working |
| `tools.xenon.lan`, `llm.xenon.lan`, `library.xenon.lan`, `chat.xenon.lan` Caddy vhosts | Those service UI/API origins become unreachable by hostname |
| `scripts/verify_deployment.py`, several tests using `xenon.lan` as a synthetic origin | Verification and fixtures need an IP-based replacement |

Staged retirement plan (needs an operator-approved edge change window):

1. Stop advertising `.lan` in README/docs and provide IP-based URLs.
2. Add IP-based access for `tools`/`llm`/`chat` where a browser UI is needed
   (different ports on `192.168.0.101`, or path routes on the existing 9443 site).
3. Migrate the deploy executor and verification scripts to the IP endpoint.
4. Remove the `.lan` site addresses and the `.lan` origin→issuer mapping, then
   verify LAN login, deploys and service health.

Until steps 2–3 exist, deleting the `.lan` vhosts would break deployments and LAN
login. The follow-up work can be tracked as a dedicated issue once this increment
is deployed.
