# OpenWebUI AI chat app

OpenWebUI runs on VM100 as the rootless Podman unit
`xeonolab-openwebui-production` (Open WebUI v0.12.3). Its quadlet
(`/etc/containers/systemd/xeonolab-openwebui-production.container`, operator-managed,
not a repository file) must contain:

```ini
PublishPort=192.168.0.100:3000:8080
Environment=CORS_ALLOW_ORIGIN=https://chat.xenon.lan;https://chat.jyxxe.com;https://chat.xenon.jyxxe.com
Environment=WEBUI_SESSION_COOKIE_SAME_SITE=none
Environment=WEBUI_SESSION_COOKIE_SECURE=true
Environment=ENABLE_OLLAMA_API=false
```

- `PublishPort` on the LAN address is required; the previous loopback-only publish
  (`127.0.0.1:3000`) made the service unreachable from the ingress.
- `WEBUI_SESSION_COOKIE_SAME_SITE=none` + `WEBUI_SESSION_COOKIE_SECURE=true` are
  required for the cross-site desktop iframe login to persist. Open WebUI's
  default is `SameSite=lax` with `Secure=false`; `Lax` cookies are not sent on
  cross-site subresources, which strands the iframe on the login page after
  sign-in.
- `ENABLE_OLLAMA_API=false` removes the `/ollama/api/version` probe noise on a
  deployment that only uses the OpenAI-compatible model connection (see below).
  It does not affect that connection.

VM101 Caddy exposes OpenWebUI as a separate application origin:

- `https://chat.xenon.lan` in `xeonolab.caddy` (LAN entry, internal TLS,
  `frame-ancestors` limited to the LAN desktop; operator-managed file).
- `@chat host chat.xenon.jyxxe.com chat.jyxxe.com` in
  `infra/network/cloudflare-origin.caddy` (public handler, no `X-Frame-Options`,
  forwards `X-Forwarded-Proto: https` and the original `Host`). Activation
  installs this public include on VM101, so the host list is deployable.

## Supported entries and known noise

Use one of these entries; do not bookmark the raw container port:

| Entry | Notes |
|---|---|
| `https://chat.xenon.lan` | LAN only, internal TLS. The desktop `AI 对话` app embeds this origin. |
| `https://chat.jyxxe.com` | Public, first-level host covered by the zone's Universal SSL certificate. Preferred public entry. |
| `https://chat.xenon.jyxxe.com` | Public, second-level host; needs Total TLS or an advanced certificate for `*.xenon.jyxxe.com`, otherwise the Cloudflare edge rejects the TLS handshake. |
| `http://192.168.0.100:3000` | **Unsupported for browsers.** The session cookie is `Secure`, so it is not sent over plain HTTP: profile images return 401 and socket.io falls back to `ws://` and fails. Use an HTTPS entry. |

The following console messages on a supported HTTPS entry are expected and are
not service failures: 401 on `/api/v1/users/<id>/profile/image` or
`/api/v1/models/model/profile/image` while unauthenticated, 403 on
`/api/v1/auths/api_key` for an OIDC user without API-key permission, and the
Chrome `manifest.json` enctype warning raised by the upstream Open WebUI manifest
(it has no effect on page or chat behaviour). The `/ollama/api/version` 500 is
removed by `ENABLE_OLLAMA_API=false` above. `ws://` (rather than `wss://`)
socket.io attempts mean the page itself was loaded over HTTP; enable *Always Use
HTTPS* at the Cloudflare edge.

## Enabling the public entry (Cloudflare account side)

The repository cannot apply these steps: the tunnel is remotely managed and the
deployment environment only holds the connector token, not a Cloudflare API
token. Do them in the Cloudflare dashboard for the `jyxxe.com` zone:

1. **DNS** — add a proxied record for the chosen host (`chat.jyxxe.com`
   recommended) pointing at the existing tunnel, i.e. a CNAME to
   `<tunnel-id>.cfargotunnel.com` with the orange cloud enabled. A plain DNS
   record to `192.168.0.101` does not traverse the tunnel.
2. **Tunnel ingress** — Zero Trust → Networks → Tunnels → the lab tunnel →
   Public Hostname → add subdomain `chat`, domain `jyxxe.com`, service
   `HTTP://192.168.0.101:8080`. The expected ingress list is mirrored in
   `infra/network/cloudflare-routes.json`. Adding only the DNS record still
   returns a Cloudflare 530/1033 error because no ingress rule matches.
3. **Always Use HTTPS** — SSL/TLS → Edge Certificates → enable it, so the chat
   page and socket.io use `https`/`wss` after redirects.
4. If `chat.xenon.jyxxe.com` is preferred instead, first enable Total TLS (or
   upload a certificate for `*.xenon.jyxxe.com`), then repeat steps 1–3 with that
   hostname. Caddy already accepts both names.

No repository change is needed to pick `chat.jyxxe.com`; the Caddy site block and
`@chat` matcher already include it.

### Verification

From VM101 (or any LAN host, for the LAN entry):

```bash
curl -sS -o /dev/null -w 'lan %{http_code}\n' https://chat.xenon.lan/
curl -sS -o /dev/null -w 'public %{http_code}\n' https://chat.jyxxe.com/
curl -sS -o /dev/null -w 'manifest %{http_code}\n' https://chat.jyxxe.com/manifest.json
curl -sSI https://chat.jyxxe.com/ | grep -iE 'strict-transport-security|x-content-type-options|referrer-policy'
curl -sS -o /dev/null -w 'ws %{http_code}\n' \
  -H 'Connection: Upgrade' -H 'Upgrade: websocket' \
  -H 'Sec-WebSocket-Version: 13' -H 'Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==' \
  'https://chat.jyxxe.com/ws/socket.io/?EIO=4&transport=websocket'
```

Expected: `200` for the page and manifest, the three edge security headers, and
`101` for the WebSocket upgrade. In a browser, sign in on the public host and
confirm the model selector shows `~deepseek/deepseek-flash-latest` and a chat
completion renders. Issue #284 tracks the public-entry follow-up.

## Desktop app registration

The desktop app registry stores the application as `chat` with origin
`https://chat.xenon.lan` (managed through `POST /api/v1/apps` with a
`desktop:admin` token; it is runtime state in `desktop.sqlite`, not a repository
file). The XeLibrary "Ask AI" hand-off doorbell and the shell Chat window both
look up the `chat` app id.

Public desktops cannot load a LAN origin. Once a public hostname above is
verified, either switch the registered origin to it (LAN desktop then depends on
the public path) or register a second app entry for public desktops. The
`frame-ancestors` CSP on the LAN vhost only allows the LAN desktop origins, so a
public desktop embedding `https://chat.xenon.lan` is not a supported
configuration.

## Embedding requirements verified on the LAN

The desktop shell embeds the chat origin in a cross-site iframe
(`https://192.168.0.101:9443` or `https://xenon.lan` inside the shell,
`https://chat.xenon.lan` inside the frame). In addition to the cookie settings
listed above:

- The desktop app registry entry must use `https://chat.xenon.lan` as origin and
  the Caddy vhost must keep the `frame-ancestors` CSP limited to desktop shell
  origins (`https://xenon.lan`, `https://192.168.0.101:9443`). Do not add
  `X-Frame-Options: DENY`.

## Model connection

The model connection is runtime state in the Open WebUI database (admin
settings, OpenAI-compatible connection), not a repository file. On the
production volume `/srv/openwebui/production-v0.12.3/data/webui.db` the working
connection is:

- base URL `https://api.jyxxe.com/v1` (the lab LLM provider proxy; gateway
  provider credentials are on VM106 in `/etc/xeonolab/llm-provider-key`),
- model id `~deepseek/deepseek-flash-latest`,
- bearer key from the same provider credential. The gateway's own OIDC-protected
  service on VM106:8105 cannot be used directly because Open WebUI cannot rotate
  OIDC access tokens.

External models are discovered dynamically, so an empty `model` table is
expected; administrators still need a valid key for `/v1/models` to return
entries and for chat completions to succeed.

## Acceptance evidence

Acceptance check (2026-10-21): sign in to the desktop at
`https://192.168.0.101:9443/`, open the `AI 对话` dock, sign in inside the iframe
with an Open WebUI account, confirm the model selector shows
`~deepseek/deepseek-flash-latest`, send a message and confirm the rendered
assistant reply (`POST /api/chat/completions` returns 200). LAN scope of issue
#277 is closed; the public Cloudflare DNS/ingress steps above remain account-side
work and are tracked by issue #284.
