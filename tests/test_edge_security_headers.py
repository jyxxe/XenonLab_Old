"""Contract: LAN and public edge Caddy configs declare the same security headers.

Issue #94 found the public edge missing the LAN hardening. The first increment
shared one snippet file between both configs, but activation on the ingress VM
only installs `infra/network/cloudflare-origin.caddy`; an imported sibling file
does not exist in `/etc/caddy`, so the activation-time Caddy reload failed and
the deployment rolled back. This deploy-compatible revision keeps the headers
inline in both files and enforces the no-drift requirement with this contract
test instead of a runtime file import: both site blocks must declare exactly
the same required header set with exact values, and neither config may import a
file that the activation path does not install.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SITE_FILES = {
    "lan": REPO_ROOT / "infra" / "network" / "xeonolab.caddy",
    "public": REPO_ROOT / "infra" / "network" / "cloudflare-origin.caddy",
}

REQUIRED_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Strict-Transport-Security": '"max-age=300"',
}


def _site_text(filename: str, site_marker: str) -> str:
    """Return only the brace-scoped block that starts at site_marker."""
    text = (REPO_ROOT / "infra" / "network" / filename).read_text()
    start = text.index("{", text.index(site_marker))
    depth = 0
    for index in range(start, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"unbalanced braces in {filename} after {site_marker!r}")


def _headers(text: str) -> dict[str, str]:
    headers: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("header "):
            continue
        parts = stripped.split()
        if len(parts) >= 2:
            headers[parts[1]] = " ".join(parts[2:])
    return headers


def test_both_sites_declare_every_required_header_with_exact_value():
    for label, path in SITE_FILES.items():
        site_marker = "(xeonolab-desktop)" if label == "lan" else "http://xenon.jyxxe.com:8080"
        declared = _headers(_site_text(path.name, site_marker))
        for header, value in REQUIRED_HEADERS.items():
            assert declared.get(header) == value, f"{label} must declare {header} {value!r}, got {declared.get(header)!r}"


def test_site_header_sets_do_not_drift():
    # The issue is drift between the two edges; a shared runtime file is not
    # deployable by the current activation path, so the contract is the guard.
    lan_marker = "(xeonolab-desktop)"
    public_marker = "http://xenon.jyxxe.com:8080"
    lan = _headers(_site_text(SITE_FILES["lan"].name, lan_marker))
    public = _headers(_site_text(SITE_FILES["public"].name, public_marker))
    for header in REQUIRED_HEADERS:
        assert lan.get(header) == public.get(header), f"header {header!r} drifted: LAN={lan.get(header)!r} public={public.get(header)!r}"


def test_configs_do_not_import_files_outside_the_activation_inventory():
    # Activation installs only cloudflare-origin.caddy; an imported sibling
    # fails Caddy reload during activation (observed rollback on 2026-10-22).
    for label, path in SITE_FILES.items():
        text = path.read_text()
        assert "security-headers.caddy" not in text, f"{label} config must not import a non-deployed snippet file"
        assert "import security_headers" not in text, f"{label} config must expand headers inline"
