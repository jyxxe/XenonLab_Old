# Issue #284: public OpenWebUI chat entry readiness

Baseline `b70631ad9e` (`origin/feature/platform-v1`; authored on
`d78b50e2b4c1111d96567696084d143d6222e17e` and rebased twice as the base advanced); branch
`general-agent/issue-284-openwebui-public-entry-20261022`.

Issue #284 reported three groups of symptoms: 401 profile-image requests and
`ws://` failures on `http://192.168.0.100:3000`, Cloudflare errors on
`chat.jyxxe.com` / `chat.xenon.jyxxe.com`, and an `/ollama/api/version` 500 plus a
`manifest.json` enctype console warning. Triage (issue comment of 2026-10-21)
established that the raw-port 401s and `ws://` attempts are the expected
consequence of the `Secure` session cookie over plain HTTP, that the Ollama probe
is optional-integration noise, and that the public hostname needs an
account-side Cloudflare DNS record and tunnel ingress rule.

This increment makes the repository side ready and removes one real blocker: the
public Caddy include only matched `chat.xenon.jyxxe.com`, while the zone's
Universal SSL certificate only covers the first-level `chat.jyxxe.com`. A
first-level public entry therefore could never work even after the tunnel
ingress was added.

## Changes

- `infra/network/cloudflare-origin.caddy`: add `http://chat.jyxxe.com:8080` to
  the public site block and extend the existing `@chat` matcher to
  `host chat.xenon.jyxxe.com chat.jyxxe.com`. Both hostnames proxy to VM100
  `192.168.0.100:3000` with `X-Forwarded-Proto: https` and the original `Host`;
  no `X-Frame-Options` is added, so the origin stays embeddable. The file is
  installed by the VM101 activation path (`caddy_sync.py`), so the change is
  deployable; Caddy validation happens during activation and rolls back on
  failure.
- `infra/network/cloudflare-routes.json`: mirror both chat hostnames in the
  tunnel ingress reference list (`http://192.168.0.101:8080` before the
  catch-all 404).
- `docs/runbooks/openwebui-chat.md`: document the complete quadlet environment
  (`CORS_ALLOW_ORIGIN` including both public hosts, cookie flags,
  `ENABLE_OLLAMA_API=false`), the supported/unsupported entries (raw HTTP port
  is not a browser entry), the exact Cloudflare DNS + tunnel ingress + Always Use
  HTTPS steps, and verification commands for page, manifest, security headers and
  the WebSocket upgrade.
- `tests/test_openwebui_public_entry.py`: static contracts for the site
  addresses, `@chat` matcher/upstream/forwarded headers, the ingress reference
  entries and the runbook's runtime requirements.

## Validation

- `uv run pytest tests/test_openwebui_public_entry.py -q`: 4 passed.
- `uv run pytest tests/test_edge_security_headers.py tests/test_caddy_sync.py -q`:
  17 passed (edge header and activation-path contracts still hold).
- `uv run ruff check tests/test_openwebui_public_entry.py`: passed.
- Repo-wide `uv run pytest -q` on the rebased base: 541 passed, 1 existing
  deprecation warning.
- `uv run ruff check xeonolab tests`: passed.
- `uv run python scripts/export_openapi.py`: passed; no schema diff (no API
  change is introduced).

Not run in this harness:

- `caddy validate` / `caddy fmt`: no Caddy binary is installed here. The config
  change is additive to an existing site block and matcher; activation validates
  it on VM101 before reload and rolls back automatically on failure (observed
  behaviour in issue #283).
- Public hostname acceptance: this harness has no Cloudflare account API token
  and no verified public edge; the DNS record + tunnel ingress rule are
  account-side actions described in the runbook.
- VM100 quadlet restart and `/ollama/api/version` re-check: production write,
  routed to the coordinator.

## Remaining work (outside this branch)

1. Account side: add a proxied DNS record for `chat.jyxxe.com` (CNAME to the
   existing tunnel) and a tunnel public-hostname ingress rule
   `chat.jyxxe.com → HTTP://192.168.0.101:8080`; enable Always Use HTTPS. Then run
   the verification block from the runbook.
2. VM100 quadlet: set `ENABLE_OLLAMA_API=false` (and `CORS_ALLOW_ORIGIN` including
   `https://chat.jyxxe.com`), `systemctl daemon-reload`, restart the unit, confirm
   `/ollama/api/version` noise is gone and the existing model connection still
   works.
3. Optional public-desktop follow-up: switch the registered `chat` app origin to
   the verified public hostname or register a second app entry; the LAN
   `frame-ancestors` CSP does not allow public desktop origins.

No API/schema, dependency, migration or production change is included. Rollback:
revert the four files; the next activation restores the previous public Caddy
include.
