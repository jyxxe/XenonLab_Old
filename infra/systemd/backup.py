#!/usr/bin/python3
"""Consistent SQLite snapshots plus attachment facts. Does not copy live WAL files."""
import shutil
import sqlite3
import sys
from pathlib import Path

source=Path(sys.argv[1]);target=Path(sys.argv[2])
target.mkdir(parents=True,exist_ok=False,mode=0o700)
for path in source.rglob('*'):
    if path.is_file() and path.suffix in ('.sqlite','.xl'):
        relative=path.relative_to(source);destination=target/relative;destination.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(path) as original,sqlite3.connect(destination) as backup:
            original.backup(backup)
if (source/'objects').exists():shutil.copytree(source/'objects',target/'objects')
print('SQLite snapshot completed')
