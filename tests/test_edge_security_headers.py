"""Both edges expose the same baseline security headers (issue #94)."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative):
    return (ROOT / relative).read_text()


def test_lan_and_public_edges_share_baseline_security_headers():
    lan = read("infra/network/xeonolab.caddy")
    public = read("infra/network/cloudflare-origin.caddy")
    for name, config in (("lan", lan), ("public", public)):
        assert "X-Content-Type-Options nosniff" in config, name
        assert "Referrer-Policy no-referrer" in config, name
        assert "Strict-Transport-Security" in config, name


def test_public_origin_has_explicit_frame_ancestor_policy():
    public = read("infra/network/cloudflare-origin.caddy")
    assert "Content-Security-Policy" in public
    assert "frame-ancestors 'self'" in public
    assert "https://xenon.jyxxe.com" in public
    # LAN keeps its own explicit allow-list and must not silently lose it.
    lan = read("infra/network/xeonolab.caddy")
    assert 'frame-ancestors https://xenon.lan' in lan
