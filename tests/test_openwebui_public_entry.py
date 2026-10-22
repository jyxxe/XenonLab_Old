"""Contract: the public chat entry is ready at both supported hostnames.

Issue #284 covers the OpenWebUI chat entry. The public blocker is Cloudflare
account-side (DNS record + tunnel ingress), but the repository owns the VM101
public Caddy include that activation installs. That include must accept the
first-level ``chat.jyxxe.com`` host (covered by the zone's Universal SSL
certificate) as well as ``chat.xenon.jyxxe.com``, and the ingress reference list
must mirror the hostnames an operator has to add in the tunnel dashboard.

These tests are static contracts so a future edit cannot silently drop one of
the hostnames, the upstream target, or the documented runtime requirements.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_CADDY = REPO_ROOT / "infra" / "network" / "cloudflare-origin.caddy"
ROUTES = REPO_ROOT / "infra" / "network" / "cloudflare-routes.json"
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "openwebui-chat.md"

PUBLIC_ORIGIN = "http://192.168.0.101:8080"
CHAT_UPSTREAM = "192.168.0.100:3000"
CHAT_HOSTS = {"chat.jyxxe.com", "chat.xenon.jyxxe.com"}


def _site_addresses(text: str) -> list[str]:
    """Return the comma-separated addresses of the public site block."""
    for line in text.splitlines():
        if "http://xenon.jyxxe.com:8080" not in line:
            continue
        match = re.match(r"^(?P<addresses>[^{]+)\{", line.strip())
        if not match:
            continue
        return [item.strip() for item in match.group("addresses").split(",")]
    raise AssertionError("public site block not found")


def _block_after(text: str, marker: str) -> str:
    """Return the brace-scoped block that starts at ``marker``."""
    start = text.index("{", text.index(marker))
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unbalanced braces after {marker!r}")


def test_public_site_block_accepts_both_chat_hosts():
    addresses = _site_addresses(PUBLIC_CADDY.read_text())
    assert "http://*.xenon.jyxxe.com:8080" in addresses
    assert "http://chat.jyxxe.com:8080" in addresses, (
        "first-level chat.jyxxe.com must be a site address; otherwise Caddy would "
        "not match the Host header sent by the Cloudflare tunnel"
    )
    assert "http://xenon.jyxxe.com:8080" in addresses


def test_chat_matcher_routes_both_hosts_to_openwebui():
    text = PUBLIC_CADDY.read_text()
    matcher = re.search(r"^\s*@chat host (?P<hosts>[^\n]+)$", text, re.MULTILINE)
    assert matcher is not None, "@chat host matcher not found"
    assert {host.strip() for host in matcher.group("hosts").split()} == CHAT_HOSTS

    handler = _block_after(text, "@chat host")
    assert f"reverse_proxy {CHAT_UPSTREAM}" in handler
    assert "header_up X-Forwarded-Proto https" in handler
    assert "header_up X-Forwarded-Host {host}" in handler
    assert "X-Frame-Options" not in handler, "the iframe origin must stay embeddable"


def test_ingress_reference_lists_both_chat_hostnames():
    config = json.loads(ROUTES.read_text())["config"]
    ingress = config["ingress"]
    by_host = {
        rule["hostname"]: rule["service"] for rule in ingress if "hostname" in rule
    }
    for host in CHAT_HOSTS:
        assert by_host.get(host) == PUBLIC_ORIGIN, (
            f"tunnel ingress reference must route {host} to {PUBLIC_ORIGIN}"
        )
    assert ingress[-1].get("service") == "http_status:404", (
        "keep the catch-all 404 ingress rule last"
    )


def test_runbook_documents_the_required_public_and_runtime_steps():
    runbook = RUNBOOK.read_text()
    required = [
        "chat.jyxxe.com",
        "chat.xenon.jyxxe.com",
        PUBLIC_ORIGIN,
        "WEBUI_SESSION_COOKIE_SAME_SITE=none",
        "WEBUI_SESSION_COOKIE_SECURE=true",
        "ENABLE_OLLAMA_API=false",
        "Always Use HTTPS",
        "192.168.0.100:3000",
        "Unsupported for browsers",
        "#284",
    ]
    lowered = runbook.lower()
    for phrase in required:
        assert phrase.lower() in lowered, f"runbook must document {phrase!r}"
