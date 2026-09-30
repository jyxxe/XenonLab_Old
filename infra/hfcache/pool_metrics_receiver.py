#!/usr/bin/env python3
"""Small HMAC-authenticated receiver for PVE physical-pool capacity samples."""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import ipaddress
import json
import os
import re
import stat
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

MAX_BODY_BYTES = 4096
MAX_AGE_SECONDS = 120
PATH = "/api/v1/pool-metrics"
FIELDS = {"pool", "total", "used", "free", "observed_at"}
POOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SIGNATURE = re.compile(r"^v1=[0-9a-f]{64}$")


class Reject(ValueError):
    """A request that must not change the published metrics file."""


def signing_bytes(timestamp: str, body: bytes) -> bytes:
    return b"v1\nPOST\n" + PATH.encode("ascii") + b"\n" + timestamp.encode("ascii") + b"\n" + body


def parse_timestamp(value: object) -> int:
    if not isinstance(value, str) or not UTC_TIMESTAMP.fullmatch(value):
        raise Reject("invalid observed_at")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise Reject("invalid observed_at") from exc
    return int(parsed.timestamp())


def validate_payload(body: bytes, timestamp_header: str, now: int, allowed_pools: frozenset[str]) -> dict:
    if len(body) > MAX_BODY_BYTES:
        raise Reject("body too large")
    try:
        data = json.loads(body, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, ValueError) as exc:
        raise Reject("malformed JSON") from exc
    if not isinstance(data, dict) or set(data) != FIELDS:
        raise Reject("invalid schema")
    pool = data["pool"]
    if not isinstance(pool, str) or not POOL_NAME.fullmatch(pool) or pool not in allowed_pools:
        raise Reject("pool not allowed")
    for field in ("total", "used", "free"):
        # bool is an int subclass in Python, so require the exact type.
        if type(data[field]) is not int or data[field] < 0:
            raise Reject("invalid byte count")
    if data["total"] <= 0 or data["used"] + data["free"] != data["total"]:
        raise Reject("inconsistent byte counts")
    if not timestamp_header.isdecimal() or len(timestamp_header) > 12:
        raise Reject("invalid timestamp header")
    request_time = int(timestamp_header)
    if request_time > now or now - request_time > MAX_AGE_SECONDS:
        raise Reject("stale timestamp")
    if parse_timestamp(data["observed_at"]) != request_time:
        raise Reject("timestamp mismatch")
    return data


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _check_secure_destination(path: Path, require_root: bool = False) -> None:
    parent = path.parent
    parent_stat = parent.stat()
    expected_owner = 0 if require_root else os.geteuid()
    if parent_stat.st_uid != expected_owner or parent_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("metrics directory must be service-owned (root in deployment) and not group/world writable")
    try:
        target_stat = path.lstat()
    except FileNotFoundError:
        return
    if not stat.S_ISREG(target_stat.st_mode) or target_stat.st_uid != expected_owner or target_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise RuntimeError("existing metrics file must be service-owned (root in deployment), regular, and not group/world writable")


def _read_existing(path: Path) -> dict | None:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    if len(raw) > MAX_BODY_BYTES:
        raise RuntimeError("existing metrics file is oversized")
    try:
        existing = json.loads(raw, object_pairs_hook=_unique_object)
        if not isinstance(existing, dict) or set(existing) != FIELDS:
            raise ValueError
        return existing
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        # A damaged file must be repaired by an operator; do not reset replay state.
        raise RuntimeError("existing metrics file is invalid; refusing to overwrite") from exc


def _atomic_write(path: Path, data: dict) -> None:
    serialized = (json.dumps(data, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "wb", closefd=True) as output:
            output.write(serialized)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_name, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


class PoolMetricsStore:
    """Validate and durably publish each sample, preserving stale data on failure."""

    def __init__(
        self,
        path: Path,
        secret: bytes,
        allowed_pools: frozenset[str],
        clock: Callable[[], int] = lambda: int(time.time()),
        require_root: bool = False,
    ) -> None:
        self.path = path
        self.secret = secret
        self.allowed_pools = allowed_pools
        self.clock = clock
        self.require_root = require_root
        self._thread_lock = threading.Lock()

    def accept(self, body: bytes, timestamp: str, signature: str) -> None:
        if len(body) > MAX_BODY_BYTES:
            raise Reject("body too large")
        if not timestamp.isascii() or not timestamp.isdecimal() or not SIGNATURE.fullmatch(signature):
            raise Reject("invalid authentication headers")
        expected = "v1=" + hmac.new(self.secret, signing_bytes(timestamp, body), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise Reject("invalid signature")
        data = validate_payload(body, timestamp, self.clock(), self.allowed_pools)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with self._thread_lock:
            lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                os.fchmod(lock_fd, 0o600)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                _check_secure_destination(self.path, require_root=self.require_root)
                existing = _read_existing(self.path)
                if existing is not None:
                    if (
                        not isinstance(existing.get("pool"), str)
                        or existing["pool"] not in self.allowed_pools
                        or any(type(existing.get(field)) is not int for field in ("total", "used", "free"))
                        or existing["total"] <= 0
                        or existing["used"] < 0
                        or existing["free"] < 0
                        or existing["used"] + existing["free"] != existing["total"]
                    ):
                        raise RuntimeError("existing metrics file is inconsistent; refusing to overwrite")
                    old_timestamp = parse_timestamp(existing.get("observed_at"))
                    new_timestamp = parse_timestamp(data["observed_at"])
                    if new_timestamp <= old_timestamp:
                        raise Reject("timestamp is not newer than published sample")
                _atomic_write(self.path, data)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)


def configured_store() -> tuple[PoolMetricsStore, str, int]:
    secret_text = os.environ.get("POOL_TELEMETRY_HMAC_SECRET", "")
    path_text = os.environ.get("POOL_METRICS_FILE", "")
    pool_text = os.environ.get("POOL_TELEMETRY_ALLOWED_POOLS", "")
    bind = os.environ.get("POOL_TELEMETRY_BIND_ADDRESS", "")
    port_text = os.environ.get("POOL_TELEMETRY_PORT", "")
    if not secret_text or not path_text or not pool_text or not bind or not port_text:
        raise RuntimeError("required pool telemetry configuration is missing")
    secret = secret_text.encode("utf-8")
    if len(secret) < 32:
        raise RuntimeError("HMAC secret must contain at least 32 bytes")
    pools = frozenset(item.strip() for item in pool_text.split(",") if item.strip())
    if not pools or any(not POOL_NAME.fullmatch(pool) or pool.startswith("-") for pool in pools):
        raise RuntimeError("allowed pool configuration is invalid")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise RuntimeError("receiver port is invalid") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("receiver port is invalid")
    try:
        bind_ip = ipaddress.ip_address(bind)
    except ValueError as exc:
        raise RuntimeError("receiver bind address must be a private interface IP") from exc
    if not bind_ip.is_private or bind_ip.is_unspecified or bind_ip.is_loopback or bind_ip.is_multicast:
        raise RuntimeError("receiver bind address must be a private interface IP")
    path = Path(path_text)
    if not path.is_absolute() or not path.parent.is_dir():
        raise RuntimeError("metrics file must be an absolute path in an existing directory")
    if os.geteuid() != 0:
        raise RuntimeError("receiver must run as root to publish a root-owned metrics file")
    _check_secure_destination(path, require_root=True)
    return PoolMetricsStore(path, secret, pools, require_root=True), bind, port


class Receiver(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def handler_for(store: PoolMetricsStore):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format: str, *args: object) -> None:
            # No access logging: headers and payloads are never written to service logs.
            return

        def _reply(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

        def do_POST(self) -> None:
            if self.path != PATH or self.headers.get("Transfer-Encoding") is not None:
                self._reply(404)
                return
            length_text = self.headers.get("Content-Length", "")
            if not length_text.isdecimal() or len(length_text) > 8:
                self._reply(400)
                return
            length = int(length_text)
            if length > MAX_BODY_BYTES:
                self._reply(413)
                return
            timestamp = self.headers.get("X-XeonoLab-Timestamp", "")
            signature = self.headers.get("X-XeonoLab-Signature", "")
            body = self.rfile.read(length)
            if len(body) != length:
                self._reply(400)
                return
            try:
                store.accept(body, timestamp, signature)
            except Reject:
                self._reply(401)
                return
            except (OSError, RuntimeError, ValueError):
                self._reply(500)
                return
            self._reply(204)

        def do_GET(self) -> None:
            self._reply(404)

        def do_PUT(self) -> None:
            self._reply(404)

        def do_DELETE(self) -> None:
            self._reply(404)

    return Handler


def main() -> int:
    try:
        store, bind, port = configured_store()
        server = Receiver((bind, port), handler_for(store))
    except (OSError, RuntimeError, ValueError):
        print("pool metrics receiver configuration or bind failed", file=sys.stderr)
        return 1
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
