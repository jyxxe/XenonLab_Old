#!/usr/bin/env python3
"""Send physical ZFS pool capacity to the configured HF-cache receiver."""

from __future__ import annotations

import hashlib
import hmac
import http.client
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_RESPONSE_BYTES = 4096
POOL_NAME = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")
ZPOOL_COMMAND = "/sbin/zpool"
RECEIVER_PATH = "/api/v1/pool-metrics"


class CollectorError(ValueError):
    pass


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise CollectorError(f"required configuration is missing: {name}")
    return value


def configured_pool() -> str:
    pool = _required_env("POOL_TELEMETRY_POOL")
    if not POOL_NAME.fullmatch(pool) or pool.startswith("-"):
        raise CollectorError("POOL_TELEMETRY_POOL is not a valid pool name")
    return pool


def collect(pool: str, run=subprocess.run) -> dict[str, str | int]:
    """Run only the fixed zpool list command and parse its one requested row."""
    if not POOL_NAME.fullmatch(pool) or pool.startswith("-"):
        raise CollectorError("invalid pool name")
    try:
        result = run(
            [ZPOOL_COMMAND, "list", "-H", "-p", "-o", "name,size,alloc,free", pool],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise CollectorError("zpool list failed") from exc
    rows = result.stdout.splitlines()
    if len(rows) != 1:
        raise CollectorError("zpool list returned an unexpected number of rows")
    columns = rows[0].split("\t")
    if len(columns) != 4 or columns[0] != pool:
        raise CollectorError("zpool list returned an unexpected row")
    try:
        total, used, free = (int(value, 10) for value in columns[1:])
    except ValueError as exc:
        raise CollectorError("zpool list returned invalid byte counts") from exc
    if total <= 0 or used < 0 or free < 0 or used + free != total:
        raise CollectorError("zpool list returned inconsistent byte counts")
    observed_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {"pool": pool, "total": total, "used": used, "free": free, "observed_at": observed_at}


def signed_headers(payload: bytes, secret: bytes, timestamp: int) -> dict[str, str]:
    signature = hmac.new(secret, b"v1\nPOST\n" + RECEIVER_PATH.encode() + b"\n" + str(timestamp).encode() + b"\n" + payload, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "Content-Length": str(len(payload)),
        "X-XeonoLab-Timestamp": str(timestamp),
        "X-XeonoLab-Signature": f"v1={signature}",
        "Connection": "close",
    }


def send(payload: dict[str, str | int], receiver_url: str, secret: bytes, now: int | None = None) -> None:
    parts = urlsplit(receiver_url)
    if parts.scheme != "http" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise CollectorError("POOL_TELEMETRY_RECEIVER_URL must be an HTTP URL without credentials or query")
    if parts.path not in ("", "/", RECEIVER_PATH):
        raise CollectorError("receiver URL path must be the fixed pool-metrics endpoint")
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if now is None:
        observed_at = payload.get("observed_at")
        if not isinstance(observed_at, str):
            raise CollectorError("sample has no valid observation timestamp")
        try:
            timestamp = int(datetime.strptime(observed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        except ValueError as exc:
            raise CollectorError("sample has no valid observation timestamp") from exc
    else:
        timestamp = now
    connection = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=10)
    try:
        connection.request("POST", RECEIVER_PATH, body=body, headers=signed_headers(body, secret, timestamp))
        response = connection.getresponse()
        response.read(MAX_RESPONSE_BYTES + 1)
        if response.status != 204:
            raise CollectorError(f"receiver rejected telemetry (HTTP {response.status})")
    except (OSError, http.client.HTTPException) as exc:
        raise CollectorError("receiver request failed") from exc
    finally:
        connection.close()


def main() -> int:
    try:
        pool = configured_pool()
        secret = _required_env("POOL_TELEMETRY_HMAC_SECRET").encode("utf-8")
        if len(secret) < 32:
            raise CollectorError("POOL_TELEMETRY_HMAC_SECRET must contain at least 32 bytes")
        receiver = _required_env("POOL_TELEMETRY_RECEIVER_URL")
        send(collect(pool), receiver, secret)
    except CollectorError as exc:
        # Deliberately omit exception details from subprocess/HTTP errors and configuration values.
        print(f"pool telemetry collection failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
