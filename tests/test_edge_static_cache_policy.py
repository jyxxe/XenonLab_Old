"""Contract: static assets are cached immutably and the SPA entry is revalidated.

Issue #293 is about Xenon Panel intranet load speed. The Vite build emits
content-hashed files under ``/assets`` but both edges served them without any
``Cache-Control`` header, so a browser could not treat an unchanged bundle as
cacheable across navigations and re-downloaded the ~0.6 MB JavaScript on each
load. The SPA entry (``/`` and ``/index.html``) must stay revalidated so a new
release is picked up immediately.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
EDGE_FILES = (
    REPO_ROOT / "infra" / "network" / "xeonolab.caddy",
    REPO_ROOT / "infra" / "network" / "cloudflare-origin.caddy",
)

IMMUTABLE = 'Cache-Control "public, max-age=31536000, immutable"'
NO_CACHE = 'Cache-Control "no-cache"'


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_hashed_assets_are_immutable_on_both_edges():
    for path in EDGE_FILES:
        text = _text(path)
        assert "@immutable_assets path /assets/*" in text, f"{path.name} must scope caching to hashed assets"
        assert IMMUTABLE in text, f"{path.name} must mark hashed assets immutable"
        # The immutable policy must not leak onto API or document paths.
        for line in text.splitlines():
            if IMMUTABLE in line:
                assert "@immutable_assets" in line, f"{path.name} must not apply immutable caching globally"


def test_spa_entry_stays_revalidated_on_both_edges():
    for path in EDGE_FILES:
        text = _text(path)
        assert "@spa_entry path / /index.html" in text, f"{path.name} must target the SPA entry"
        for line in text.splitlines():
            if NO_CACHE in line:
                assert "@spa_entry" in line, f"{path.name} must apply no-cache to the SPA entry only"


def test_edge_headers_keep_the_required_security_set():
    for path in EDGE_FILES:
        text = _text(path)
        assert "X-Content-Type-Options nosniff" in text
        assert "Referrer-Policy no-referrer" in text
        assert 'Strict-Transport-Security "max-age=300"' in text
