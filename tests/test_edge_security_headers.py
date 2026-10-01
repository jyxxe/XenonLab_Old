"""Contract: LAN and public edge Caddy configs must declare the same security headers.

Issue #94 found the public edge (cloudflare-origin.caddy) missing the
X-Content-Type-Options / Referrer-Policy hardening that the LAN desktop
snippet already had. Both files must now declare the identical set so the
public origin is not softer than LAN and the two configs cannot drift again.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

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


def test_lan_and_public_desktop_sites_declare_identical_security_headers():
    lan = _site_text("xeonolab.caddy", "(xeonolab-desktop)")
    public = _site_text("cloudflare-origin.caddy", "http://xenon.jyxxe.com:8080")
    for filename, site in (("xeonolab.caddy", lan), ("cloudflare-origin.caddy", public)):
        for header, value in REQUIRED_HEADERS.items():
            line = f"header {header} {value}"
            assert line in site, f"{filename} must declare {line!r}"


def test_no_other_security_header_drift_between_the_two_files():
    observed = {}
    for filename, site_marker in (
        ("xeonolab.caddy", "(xeonolab-desktop)"),
        ("cloudflare-origin.caddy", "http://xenon.jyxxe.com:8080"),
    ):
        site = _site_text(filename, site_marker)
        observed[filename] = {
            line.split()[1] for line in site.splitlines() if line.strip().startswith("header ")
        }
    lan_only = observed["xeonolab.caddy"] - observed["cloudflare-origin.caddy"]
    public_only = observed["cloudflare-origin.caddy"] - observed["xeonolab.caddy"]
    assert not lan_only, f"LAN declares headers the public edge lacks: {sorted(lan_only)}"
    assert not public_only, f"public edge declares headers LAN lacks: {sorted(public_only)}"
