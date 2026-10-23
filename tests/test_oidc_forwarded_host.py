"""Contract: auth proxy must forward a port-bearing host to Keycloak.

Keycloak derives its frontend URL (and therefore the token `iss`) from the
trusted `X-Forwarded-Host` header. Caddy's `{host}` placeholder strips the port,
so a direct-IP request to `https://192.168.0.101:9444` made Keycloak advertise
`https://192.168.0.101/realms/xeonolab`, which no longer matched the deploy
gateway's `OIDC_ISSUER=https://192.168.0.101:9444/realms/xeonolab`. Release
creation then failed with 401 (observed 2026-10-22 after the LAN include was
synced with `{host}`).

`{http.request.hostport}` preserves the port for ported hosts and equals the
plain host for default-port hosts, so both edges must use it.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EDGE_FILES = (
    REPO_ROOT / "infra" / "network" / "xeonolab.caddy",
    REPO_ROOT / "infra" / "network" / "cloudflare-origin.caddy",
)


def test_every_forwarded_host_preserves_the_port():
    for path in EDGE_FILES:
        forwarded = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip().startswith("header_up X-Forwarded-Host")
        ]
        assert forwarded, f"{path.name} must forward X-Forwarded-Host to Keycloak"
        for line in forwarded:
            assert line.endswith("{http.request.hostport}"), (
                f"{path.name} must use {{http.request.hostport}} so the Keycloak issuer keeps its port: {line}"
            )
            assert "{host}" not in line, f"{path.name} must not use the port-stripping placeholder: {line}"


def test_auth_routes_forward_host_and_proto_together():
    for path in EDGE_FILES:
        text = path.read_text(encoding="utf-8")
        for marker in ("X-Forwarded-Host", "X-Forwarded-Proto"):
            assert marker in text, f"{path.name} must forward {marker} to Keycloak"
