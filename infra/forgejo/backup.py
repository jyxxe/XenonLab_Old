"""Snapshot the SQLite facts; Git repositories must be idle during this backup."""

import sqlite3
import tarfile
import tempfile
from pathlib import Path

with tempfile.TemporaryDirectory() as work:
    snapshot = Path(work) / "forgejo.db"
    with sqlite3.connect("/var/lib/forgejo/data/forgejo.db") as source, sqlite3.connect(snapshot) as target:
        source.backup(target)
        assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    result = Path("/tmp/xeonolab-forgejo-backup.tar.gz")
    with tarfile.open(result, "w:gz") as archive:
        archive.add(snapshot, arcname="data/forgejo.db")
        archive.add("/etc/forgejo/app.ini", arcname="config/app.ini")
        for folder in ("repositories", "data/attachments", "data/avatars", "data/repo-avatars"):
            source = Path("/var/lib/forgejo") / folder
            if source.exists():
                archive.add(source, arcname=folder)
    result.chmod(0o600)
print("Forgejo facts snapshot integrity checked")
