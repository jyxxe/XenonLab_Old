"""Helpers for copying private deployment assets without exposing contents."""

import json
import re
import shutil
import stat
from pathlib import Path


def copy_qdrant_api_key(source: Path, destination: Path) -> None:
    """Copy the Qdrant key as a private node file, never as environment text."""
    shutil.copyfile(source, destination)
    destination.chmod(0o600)


def validate_forgejo_claim_inputs(token_path: Path, users_path: Path) -> dict[str, str]:
    """Validate private local claim-sync inputs without returning/logging the token."""
    for path in (token_path, users_path):
        try:
            info = path.lstat()
        except OSError:
            raise ValueError("required private Forgejo claim input is unavailable") from None
        if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError("Forgejo claim inputs must be regular owner-only files")

    try:
        token = token_path.read_text().strip()
        mapping = json.loads(users_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Forgejo claim inputs are invalid") from None
    if not token or any(character.isspace() for character in token):
        raise ValueError("Forgejo claim inputs are invalid")
    if (
        not isinstance(mapping, dict)
        or not mapping
        or len(mapping) > 1000
        or any(
            not isinstance(subject, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,255}", subject)
            or not isinstance(username, str)
            or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", username)
            for subject, username in mapping.items()
        )
    ):
        raise ValueError("Forgejo subject mapping is invalid")
    return mapping


def copy_forgejo_claim_inputs(token_path: Path, users_path: Path, token_dest: Path, users_dest: Path) -> None:
    """Copy validated claim-sync inputs into a protected deployment payload."""
    validate_forgejo_claim_inputs(token_path, users_path)
    token_dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(token_path, token_dest)
    shutil.copyfile(users_path, users_dest)
    token_dest.chmod(0o600)
    users_dest.chmod(0o600)
