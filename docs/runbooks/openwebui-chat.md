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
