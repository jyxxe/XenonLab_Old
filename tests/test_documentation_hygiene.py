"""Repository hygiene: tracked text files must not carry merge-conflict markers.

A committed conflict marker makes documentation unreadable and hides which side
of a merge was actually intended. docs/implementation-status.md shipped with
``<<<<<<< HEAD``/``>>>>>>>`` markers once, so keep a cheap repository-wide guard
in the test suite.
"""

import subprocess
from pathlib import Path

TEXT_SUFFIXES = (
    ".md", ".py", ".ts", ".tsx", ".js", ".mjs", ".json", ".sh", ".caddy",
    ".service", ".timer", ".yaml", ".yml", ".toml", ".conf", ".css", ".html",
    ".txt", ".sql",
)


def conflict_markers(root: Path) -> list[str]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True
    )
    offenders: list[str] = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        relative = raw.decode()
        if not relative.endswith(TEXT_SUFFIXES):
            continue
        path = root / relative
        try:
            lines = path.read_text(errors="replace").splitlines(keepends=True)
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if line.startswith("<<<<<<< ") or line.startswith(">>>>>>> ") or line.rstrip("\n") == "=======":
                offenders.append(f"{relative}:{number}")
    return offenders


def test_tracked_text_files_have_no_conflict_markers():
    root = Path(__file__).parents[1]
    offenders = conflict_markers(root)
    assert not offenders, "merge-conflict markers in tracked text files: " + ", ".join(offenders[:20])
