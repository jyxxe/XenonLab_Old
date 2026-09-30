"""Safely validate and install the fixed Web 101 Cloudflare Caddy include."""

import base64
import json
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

CADDYFILE = Path("/etc/caddy/Caddyfile")
INCLUDE_TARGET = Path("/etc/caddy/cloudflare-origin.caddy")
IMPORT_LINE = re.compile(
    r"^(?P<prefix>[ \t]*import[ \t]+)/etc/caddy/cloudflare-origin\.caddy"
    r"(?P<suffix>[ \t]*(?:\#[^\r\n]*)?)(?P<newline>\r?\n|$)",
    re.MULTILINE,
)
CADDY = "/usr/bin/caddy"


def atomic_write_json(path, value):
    """Persist root-managed activation state before any service mutation."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class Snapshot:
    content: bytes
    uid: int
    gid: int
    mode: int

    def as_json(self):
        return {
            "content": base64.b64encode(self.content).decode("ascii"),
            "uid": self.uid,
            "gid": self.gid,
            "mode": self.mode,
        }

    @classmethod
    def from_json(cls, value):
        content = base64.b64decode(value["content"], validate=True)
        uid, gid, mode = value["uid"], value["gid"], value["mode"]
        if not all(isinstance(number, int) and number >= 0 for number in (uid, gid, mode)):
            raise ValueError("invalid Caddy include snapshot metadata")
        return cls(content, uid, gid, mode)


def _target_metadata(target):
    info = target.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Caddy include target must be a regular file")
    return info


def capture(target=INCLUDE_TARGET):
    info = _target_metadata(target)
    return Snapshot(target.read_bytes(), info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode))


def _candidate_caddyfile(candidate, caddyfile=CADDYFILE):
    text = caddyfile.read_text()
    replaced, count = IMPORT_LINE.subn(
        lambda match: match.group("prefix")
        + str(candidate)
        + match.group("suffix")
        + match.group("newline"),
        text,
    )
    if count != 1:
        raise ValueError("Caddyfile must contain exactly one fixed Cloudflare include import")
    return replaced


def validate(candidate, caddyfile=CADDYFILE, runner=subprocess.run):
    """Validate the complete live Caddyfile with only the fixed include substituted."""
    candidate = Path(candidate)
    info = candidate.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError("Caddy include candidate must be a regular file")
    contents = _candidate_caddyfile(candidate, caddyfile)
    fd, temporary = tempfile.mkstemp(prefix=".xeonolab-caddy-validate-", dir=caddyfile.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(contents)
        os.chmod(temporary, 0o600)
        runner([CADDY, "validate", "--config", temporary, "--adapter", "caddyfile"], check=True)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_write(target, content, uid, gid, mode, on_replace=None):
    fd, temporary = tempfile.mkstemp(prefix=".xeonolab-caddy-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, target)
        if on_replace is not None:
            on_replace()
        directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        Path(temporary).unlink(missing_ok=True)


def install(
    candidate,
    target=INCLUDE_TARGET,
    caddyfile=CADDYFILE,
    runner=subprocess.run,
    on_replace=None,
):
    """Validate first, then atomically replace the fixed include preserving metadata."""
    candidate = Path(candidate)
    info = _target_metadata(target)
    validate(candidate, caddyfile, runner)
    content = candidate.read_bytes()
    if target.read_bytes() != content:
        _atomic_write(
            target,
            content,
            info.st_uid,
            info.st_gid,
            stat.S_IMODE(info.st_mode),
            on_replace=on_replace,
        )
        return True
    return False


def restore(
    snapshot,
    target=INCLUDE_TARGET,
    caddyfile=CADDYFILE,
    runner=subprocess.run,
    on_replace=None,
):
    """Validate a saved include and atomically restore it with its original metadata."""
    snapshot = snapshot if isinstance(snapshot, Snapshot) else Snapshot.from_json(snapshot)
    fd, temporary = tempfile.mkstemp(prefix=".xeonolab-caddy-rollback-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(snapshot.content)
        validate(temporary, caddyfile, runner)
        info = _target_metadata(target)
        if (
            target.read_bytes() == snapshot.content
            and info.st_uid == snapshot.uid
            and info.st_gid == snapshot.gid
            and stat.S_IMODE(info.st_mode) == snapshot.mode
        ):
            return False
        _atomic_write(
            target,
            snapshot.content,
            snapshot.uid,
            snapshot.gid,
            snapshot.mode,
            on_replace=on_replace,
        )
        return True
    finally:
        Path(temporary).unlink(missing_ok=True)
