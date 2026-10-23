"""Admin-only credential prompt catalogue: strict loading and desktop-admin ACL."""

from pathlib import Path

from fastapi.testclient import TestClient

from xeonolab.agent_prompts import MAX_BYTES, load_admin_prompts
from xeonolab.services import create_app

from .conftest import auth


def test_loader_reads_regular_markdown_and_ignores_unsafe_entries(tmp_path: Path):
    root = tmp_path / "prompts"
    root.mkdir()
    (root / "codex-admin.md").write_text("# Codex 管理员版\n\nTOKEN=secret-value", encoding="utf-8")
    (root / "notes.txt").write_text("ignored", encoding="utf-8")
    (root / "large.md").write_text("x" * (MAX_BYTES + 1), encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("# outside", encoding="utf-8")
    (root / "link.md").symlink_to(outside)

    rows = load_admin_prompts(root)
    assert [row["id"] for row in rows] == ["codex-admin"]
    assert rows[0]["title"] == "Codex 管理员版"
    assert "secret-value" in rows[0]["body"]
    assert load_admin_prompts(tmp_path / "missing") == []


def test_loader_enforces_the_file_cap(tmp_path: Path):
    root = tmp_path / "many"
    root.mkdir()
    for index in range(40):
        (root / f"prompt-{index:02d}.md").write_text(f"# P{index}", encoding="utf-8")
    assert len(load_admin_prompts(root)) == 32


def test_agent_prompts_endpoint_requires_desktop_admin(settings, tmp_path, monkeypatch):
    root = tmp_path / "admin-prompts"
    root.mkdir()
    (root / "codex-admin.md").write_text("# Codex 管理员版\nbody", encoding="utf-8")
    monkeypatch.setenv("AGENT_PROMPTS_DIR", str(root))
    settings.dev_tokens["admin"]["scope"] += " desktop:admin desktop:read"
    settings.dev_tokens["alice"]["scope"] += " desktop:read"
    client = TestClient(create_app("desktop-web", settings))
    assert client.get("/api/v1/agent-prompts", headers=auth()).status_code == 403
    allowed = client.get("/api/v1/agent-prompts", headers=auth("admin"))
    assert allowed.status_code == 200
    assert [row["id"] for row in allowed.json()["data"]] == ["codex-admin"]
