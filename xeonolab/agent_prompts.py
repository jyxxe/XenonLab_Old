"""Admin-only, credential-bearing agent prompts stored outside the repository.

The public prompt catalogue ships in ``docs/agent-prompts`` and contains no
secrets. Operators may place credential-bearing variants in a root-managed
directory (default ``/etc/xeonolab/agent-prompts``); this module loads them for
the admin-only desktop endpoint. Only regular ``*.md`` files are read, symlinks
are ignored, and both the file count and each file size are capped.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_DIRECTORY = "/etc/xeonolab/agent-prompts"
MAX_FILES = 32
MAX_BYTES = 64 * 1024
NAME = re.compile(r"[A-Za-z0-9._-]{1,64}\.md")


def prompts_directory() -> Path:
    return Path(os.getenv("AGENT_PROMPTS_DIR", DEFAULT_DIRECTORY))


def load_admin_prompts(directory: Path | None = None) -> list[dict]:
    """Return the admin prompt files, skipping anything unsafe or oversized."""
    root = Path(directory) if directory is not None else prompts_directory()
    if root.is_symlink() or not root.is_dir():
        return []
    rows: list[dict] = []
    for entry in sorted(root.iterdir(), key=lambda item: item.name):
        if len(rows) >= MAX_FILES:
            break
        if entry.is_symlink() or not entry.is_file() or not NAME.fullmatch(entry.name):
            continue
        try:
            if entry.stat().st_size > MAX_BYTES:
                continue
            body = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        title = next((line[2:].strip() for line in body.splitlines() if line.startswith("# ")), entry.stem)
        rows.append({"id": entry.stem, "title": title[:120], "file": entry.name, "body": body})
    return rows
