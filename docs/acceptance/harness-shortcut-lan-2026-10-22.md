# DeepSeek Harness LAN-only shortcut — 2026-10-22

## Scope

XeonoLab desktop gets a built-in `DeepSeek Harness` quick entry that is only
offered when the shell is loaded from the lab LAN (same intranet as the
server). Selecting it opens the token-bearing Harness URL in a new tab; the
token is resolved at request time and is never stored in the repository, the
app registry or desktop preferences.

## Design

- Client gate: `isLanOrigin(window.location.hostname)` accepts `localhost`,
  `*.lan` and RFC1918 addresses only. Public origins (`xenon.jyxxe.com`) and
  lookalikes (`xenon.lan.example.com`, `192.168.0.101.example.com`) are
  rejected. The app is appended to `builtinApps()` only when the gate passes.
- Same-origin target: the app opens the relative LAN path `/harness`, which VM101
  Caddy serves only on the LAN desktop vhost (`https://xenon.lan`,
  `https://192.168.0.101:9443`) by proxying to `192.168.0.111:4097`.
- Token source: `dsh-harness-launch` (root systemd service on VM111) reads the
  current per-process launch token from the harness service journal, exchanges
  it for the Harness browser-session cookie server-side (single-use launch
  tokens would otherwise be consumed by the first visitor), and serves a tiny
  VM111 page that sets that cookie and refreshes to the Harness UI. The cookie
  is scoped to the Harness host and accepted over the same-site refresh;
  tokens rotate whenever the harness service restarts and no copy is persisted.
- First hop: the LAN desktop's `/harness` request is answered with a `302` to
  `http://192.168.0.111:4097/launch` so the cookie-setting page is served from
  the Harness host itself; a direct `302` to the token URL would lose the
  `SameSite=Strict` session cookie on the cross-site redirect chain.
- Network gate: the launcher binds `192.168.0.111:4097`, and
  `infra/devagent/web-firewall.nft` accepts that port only from loopback and
  `192.168.0.0/24` while rejecting other sources (same pattern as 4096). The
  Harness UI itself remains bound to the VM111 LAN address.

## Verification

- Frontend: `isLanOrigin` unit cases (LAN hostnames/private ranges vs public,
  lookalike, invalid hosts); `npx tsc --noEmit`; desktop unit suite.
- Launcher: `curl -s http://192.168.0.111:4097/health` → `200`;
  `GET /harness` → `302` with the current token in `Location`; called from
  another LAN VM succeeds, and the nft table shows the 4097 LAN accept rule.
- Ingress: on VM101 `caddy validate` passes and `reload` succeeds; `GET
  https://xenon.lan/harness` (and the LAN desktop origin `/harness`) returns
  `302`; the public origin's `/harness` never returns a token `Location`.
- Desktop acceptance (production, after deployment): LAN desktop shows
  `DeepSeek Harness` in LaunchPad and the shortcut lands on the authenticated
  Harness UI in a new tab; the public desktop does not list the app.
