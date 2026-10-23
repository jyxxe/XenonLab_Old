"""Contract: the agent prompt catalogue is complete and served without secrets.

The Xenon Panel entry (apps/desktop-web System -> 提示词) bundles the prompts,
and both Caddy edges serve the same files under /agent-prompts/ so intranet and
tunnel agents can read the current prompts without a repository checkout. This
test guards the deploy-time contract: the manifest stays complete, the prompt
files exist and are not empty, the served directory contains no credential
material, and both edge configs keep the static route pointing at the active
release.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "docs" / "agent-prompts"
MANIFEST = PROMPTS_DIR / "manifest.json"
SERVE_ROOT = "/opt/xeonolab/current/docs/agent-prompts"
EXPECTED_IDS = ("codex", "dsh", "opencode", "general-agent-lan", "general-agent-wan")
EDGE_FILES = (
    REPO_ROOT / "infra" / "network" / "xeonolab.caddy",
    REPO_ROOT / "infra" / "network" / "cloudflare-origin.caddy",
)
PRIVATE_KEY_MARKER = re.compile(r"BEGIN [A-Z ]*PRIVATE KEY")
CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(client_secret|api[_-]?key|private[_-]?key|password|token)\b\s*[:=]\s*['\"]?[A-Za-z0-9_\-/+]{16,}"
)


def _manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_manifest_lists_every_expected_prompt_with_an_existing_body():
    manifest = _manifest()
    assert isinstance(manifest.get("version"), int)
    prompts = manifest.get("prompts")
    assert isinstance(prompts, list)
    ids = tuple(prompt["id"] for prompt in prompts)
    assert ids == EXPECTED_IDS, f"manifest ids drifted: {ids!r}"
    for prompt in prompts:
        assert prompt["network"] in ("lan", "wan"), prompt
        assert prompt["title"] and prompt["audience"] and prompt["summary"], prompt
        body = (PROMPTS_DIR / prompt["file"]).read_text(encoding="utf-8")
        assert len(body.strip()) > 200, f"{prompt['file']} looks empty"
        assert body.startswith("#"), f"{prompt['file']} must start with a heading"


def test_prompt_catalogue_contains_no_credentials():
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        assert not PRIVATE_KEY_MARKER.search(text), f"{path.name} contains a private key marker"
        match = CREDENTIAL_ASSIGNMENT.search(text)
        assert match is None, f"{path.name} contains a credential-looking assignment: {match.group(0)[:40]!r}"


def test_both_edges_serve_the_prompt_catalogue_from_the_active_release():
    for path in EDGE_FILES:
        text = path.read_text(encoding="utf-8")
        assert "handle_path /agent-prompts/*" in text, f"{path.name} is missing the prompt route"
        assert f"root * {SERVE_ROOT}" in text, f"{path.name} must serve the active release prompts"
        route_start = text.index("handle_path /agent-prompts/*")
        route = text[route_start : route_start + 220]
        assert "file_server" in route, f"{path.name} prompt route must use file_server"
        assert ".." not in route, f"{path.name} prompt route must not allow parent traversal"
