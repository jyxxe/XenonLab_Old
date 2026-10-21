"""Contract: LAN and public edge Caddy configs share one security-header snippet.

Issue #94 found the public edge missing the LAN hardening. The review of the
first increment required a single shared source instead of duplicated header
lines, because copy-paste still allowed drift and same-name conflicting
values. Both site configs must import infra/network/security-headers.caddy,
expand the snippet inside the site block, and must not declare the security
headers inline (a second declaration would send conflicting duplicate values).
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SNIPPET_FILE = REPO_ROOT / "infra" / "network" / "security-headers.caddy"
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


def test_snippet_declares_every_required_header_with_exact_value():
    lines = {
        line.split()[1]: " ".join(line.split()[2:])
        for line in SNIPPET_FILE.read_text().splitlines()
        if line.strip().startswith("header ")
    }
    for header, value in REQUIRED_HEADERS.items():
        assert lines.get(header) == value, f"snippet must declare {header} {value!r}, got {lines.get(header)!r}"


def test_both_sites_import_the_shared_snippet():
    for label, path in SITE_FILES.items():
        text = path.read_text()
        assert "import security-headers.caddy" in text, f"{label} config must load the snippet file"
        site_marker = "(xeonolab-desktop)" if label == "lan" else "http://xenon.jyxxe.com:8080"
        assert "import security_headers" in _site_text(path.name, site_marker), (
            f"{label} site block must expand the shared snippet"
        )


def test_site_blocks_do_not_declare_security_headers_inline():
    """A duplicate inline declaration would conflict with the snippet values."""
    for label, path in SITE_FILES.items():
        site_marker = "(xeonolab-desktop)" if label == "lan" else "http://xenon.jyxxe.com:8080"
        site = _site_text(path.name, site_marker)
        inline = [
            line.strip() for line in site.splitlines()
            if line.strip().startswith("header ") and line.split()[1] in REQUIRED_HEADERS
        ]
        assert not inline, f"{label} site block must not redeclare security headers: {inline}"
