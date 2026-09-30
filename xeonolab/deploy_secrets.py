"""Helpers for copying private deployment assets without exposing contents."""

import shutil
from pathlib import Path


def copy_qdrant_api_key(source: Path, destination: Path) -> None:
    """Copy the Qdrant key as a private node file, never as environment text."""
    shutil.copyfile(source, destination)
    destination.chmod(0o600)
