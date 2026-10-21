# OpenWebUI AI chat app

OpenWebUI runs on VM100 as the rootless Podman unit
`xeonolab-openwebui-production` (Open WebUI v0.12.3). Its quadlet
(`/etc/containers/systemd/xeonolab-openwebui-production.container`) publishes
`192.168.0.100:3000` on the LAN and adds `https://chat.xenon.lan` /
`https://chat.xenon.jyxxe.com` to `CORS_ALLOW_ORIGIN`. The previous loopback-only
publish (`127.0.0.1:3000`) made the service unreachable from the ingress.

VM101 Caddy exposes it as a separate application origin:

- `https://chat.xenon.lan` in `xeonolab.caddy` (LAN entry, internal TLS,
  `frame-ancestors` limited to the LAN desktop).
- `@chat host chat.xenon.jyxxe.com` in `cloudflare-origin.caddy` (public host
  handler, no `X-Frame-Options`; the tunnel must route that hostname to
  `http://192.168.0.101:8080`).

The desktop app registry stores the application as `chat` with origin
`https://chat.xenon.lan` (managed through `POST /api/v1/apps` with a
`desktop:admin` token; it is runtime state in `desktop.sqlite`, not a repository
file). The XeLibrary "Ask AI" hand-off doorbell and the shell Chat window both
look up the `chat` app id.

Public users still cannot load a LAN origin. Enabling the public hostname needs
a Cloudflare DNS record for `chat.xenon.jyxxe.com` and a tunnel ingress rule to
VM101; after that the registered origin can be switched to the public URL (or a
second app can be registered for public desktops). Issue #277 tracks this.

## Embedding requirements verified on the LAN

The desktop shell embeds the chat origin in a cross-site iframe
(`https://192.168.0.101:9443` or `https://xenon.lan` inside the shell,
`https://chat.xenon.lan` inside the frame). Two runtime settings are required;
without them the iframe still renders the Open WebUI login page but a successful
sign-in does not persist, so the app appears unusable:

- The quadlet must set `Environment=WEBUI_SESSION_COOKIE_SAME_SITE=none` and
  `Environment=WEBUI_SESSION_COOKIE_SECURE=true`. Open WebUI's default is
  `SameSite=lax` with `Secure=false`; `Lax` cookies are not sent on cross-site
  subresources, which strands the iframe on the login page after sign-in.
- The desktop app registry entry must use `https://chat.xenon.lan` as origin and
  the Caddy vhost must keep the `frame-ancestors` CSP limited to desktop shell
  origins (`https://xenon.lan`, `https://192.168.0.101:9443`). Do not add
  `X-Frame-Options: DENY`.

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

Acceptance check (2026-10-21): sign in to the desktop at
`https://192.168.0.101:9443/`, open the `AI 对话` dock, sign in inside the iframe
with an Open WebUI account, confirm the model selector shows
`~deepseek/deepseek-flash-latest`, send a message and confirm the rendered
assistant reply (`POST /api/chat/completions` returns 200). LAN Scope of issue
#277 is closed; only the public Cloudflare DNS/ingress step above remains
account-side work.
