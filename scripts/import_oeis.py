#!/usr/bin/env python3
"""Idempotently import an offline OEIS bundle into XeLibrary through the public API.

The bundle is produced by ``scripts/build_oeis_bundle.py``. This importer never
reads credentials from the repository: the base URL comes from
``--base-url``/``XELIBRARY_URL`` and the bearer token from
``XELIBRARY_TOKEN``/``XELIBRARY_TOKEN_FILE``.

Safety properties:

- the bundle digest/byte size/count are verified against its manifest before any
  write, and a checkpoint is bound to that digest and target table;
- each row uses a stable ``Idempotency-Key`` so a resume after an interrupted run
  cannot duplicate rows;
- the checkpoint is written atomically (fsync, mode 0600) after every row;
- a 401/403 stops the import with progress preserved for an explicit resume; the
  importer never loops on authentication failures;
- table creation is only retried through an explicit lookup, never blindly.
"""

import argparse
import hashlib
import json
import os
import ssl
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_ENV = "XELIBRARY_TOKEN"
TOKEN_FILE_ENV = "XELIBRARY_TOKEN_FILE"
URL_ENV = "XELIBRARY_URL"
CHECKPOINT_VERSION = 1
COLUMNS = [
    {"name": "a_number", "type": "string", "nullable": False},
    {"name": "terms", "type": "text", "nullable": False},
    {"name": "term_count", "type": "int64", "nullable": False},
    {"name": "snapshot", "type": "string", "nullable": True},
]
MAX_BATCH_ROWS = 1000


class ImportError_(RuntimeError):
    """Rejected bundle/checkpoint or API response."""


def _bundle_digest(path):
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def verify_bundle(bundle_path, manifest_path=None):
    """Verify digest, byte size and sequence count against the manifest."""
    bundle_path = Path(bundle_path)
    manifest_path = Path(manifest_path) if manifest_path else bundle_path.with_name(
        bundle_path.name + ".manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest, size = _bundle_digest(bundle_path)
    if manifest.get("sha256") != digest:
        raise ImportError_("bundle sha256 does not match its manifest")
    if manifest.get("bytes") != size:
        raise ImportError_(f"bundle bytes {size} do not match manifest {manifest.get('bytes')}")
    count = 0
    with bundle_path.open("r", encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                count += 1
    if manifest.get("count") != count:
        raise ImportError_(f"bundle count {count} does not match manifest {manifest.get('count')}")
    return manifest


def iter_entries(bundle_path):
    with Path(bundle_path).open("r", encoding="utf-8") as stream:
        for number, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                raise ImportError_(f"bundle line {number} is not JSON") from None
            if (
                not isinstance(entry, dict)
                or set(entry) != {"a_number", "terms"}
                or not isinstance(entry["a_number"], str)
                or not entry["a_number"].startswith("A")
                or not isinstance(entry["terms"], str)
            ):
                raise ImportError_(f"bundle line {number} has an unexpected shape")
            yield entry


def row_values(entry, snapshot):
    return {
        "a_number": entry["a_number"],
        "terms": entry["terms"],
        "term_count": 0 if not entry["terms"].strip() else len(entry["terms"].split(",")),
        "snapshot": snapshot,
    }


class APIClient:
    """Minimal JSON client for the XeLibrary REST API."""

    def __init__(self, base_url, token, timeout=30, retry_429=5, sleeper=time.sleep, ca_file=None):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.retry_429 = retry_429
        self.sleeper = sleeper
        context = ssl.create_default_context(cafile=ca_file) if ca_file else ssl.create_default_context()
        self.opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=context)
        )

    def request(self, method, path, body=None, headers=None):
        request_headers = {"Authorization": f"Bearer {self.token}", **dict(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            request = urllib.request.Request(
                self.base + path, data=data, method=method, headers=request_headers
            )
            try:
                with self.opener.open(request, timeout=self.timeout) as response:
                    return response.status, json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as error:
                payload = {}
                try:
                    payload = json.loads(error.read().decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    pass
                if error.code == 429 and attempt < self.retry_429:
                    retry_after = error.headers.get("Retry-After") if error.headers else None
                    try:
                        delay = float(retry_after) if retry_after is not None else min(60, 2 ** (attempt + 1))
                    except (TypeError, ValueError):
                        delay = min(60, 2 ** (attempt + 1))
                    self.sleeper(max(0.0, min(60.0, delay)))
                    attempt += 1
                    continue
                return error.code, payload
            except (urllib.error.URLError, TimeoutError) as error:
                raise ImportError_(f"network failure: {error}") from None

    def ensure_table(self, path, name):
        status, payload = self.request("GET", "/api/v1/tables?limit=1000")
        if status == 200:
            data = payload.get("data") or []
            for table in data:
                if table.get("path") == path and table.get("name") == name:
                    return table["id"]
        status, payload = self.request(
            "POST",
            "/api/v1/tables",
            {"path": path, "name": name, "columns": COLUMNS},
        )
        if status == 201:
            return payload["data"]["table_id"]
        if status == 409:
            status, payload = self.request("GET", "/api/v1/tables?limit=1000")
            if status == 200:
                for table in payload.get("data") or []:
                    if table.get("path") == path and table.get("name") == name:
                        return table["id"]
        raise ImportError_(f"could not ensure table {path!r} {name!r}: HTTP {status}")

    def create_row(self, table_id, values, idempotency_key):
        status, payload = self.request(
            "POST",
            f"/api/v1/tables/{table_id}/rows",
            {"values": values},
            {"Idempotency-Key": idempotency_key},
        )
        if status == 201:
            return payload.get("data", {}).get("_row_id")
        if status == 409:
            raise ImportError_(
                f"idempotency conflict for {idempotency_key}; the target snapshot differs"
            )
        raise ImportError_(f"row import failed: HTTP {status} {payload.get('error')}")

    def list_rows(self, table_id, cursor=""):
        query = "/api/v1/tables/" + table_id + "/rows?limit=1000"
        if cursor:
            query += "&cursor=" + urllib.parse.quote(cursor)
        status, payload = self.request("GET", query)
        if status != 200:
            raise ImportError_(f"row listing failed: HTTP {status} {payload.get('error')}")
        next_cursor = (payload.get("meta") or {}).get("next_cursor") or ""
        return payload.get("data") or [], next_cursor


def load_checkpoint(path, bundle_sha, target):
    if not Path(path).is_file():
        return None
    checkpoint = json.loads(Path(path).read_text(encoding="utf-8"))
    if checkpoint.get("version") != CHECKPOINT_VERSION:
        raise ImportError_("unsupported checkpoint version")
    if checkpoint.get("bundle_sha256") != bundle_sha:
        raise ImportError_("checkpoint belongs to a different bundle")
    if checkpoint.get("target") != target:
        raise ImportError_("checkpoint belongs to a different target table")
    return checkpoint


def save_checkpoint(path, checkpoint):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        os.fchmod(handle, 0o600)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(checkpoint, stream, ensure_ascii=False, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def import_bundle(
    client, bundle_path, manifest, target, checkpoint_path, limit=None, shard_count=1, shard_index=1
):
    """Import rows after a verified bundle; returns the final checkpoint.

    ``shard_count``/``shard_index`` split a bundle across independent importers
    (each with its own credential and checkpoint). Entries are assigned to a shard
    by their 1-based bundle position, so shards never write the same row.
    """
    if shard_count < 1 or not 1 <= shard_index <= shard_count:
        raise ImportError_("shard-index must be within 1..shard-count")

    bundle_sha = manifest["sha256"]
    checkpoint = load_checkpoint(checkpoint_path, bundle_sha, target) or {
        "version": CHECKPOINT_VERSION,
        "bundle_sha256": bundle_sha,
        "snapshot": manifest.get("snapshot"),
        "target": target,
        "table_id": None,
        "processed": 0,
    }
    table_id = checkpoint.get("table_id")
    if not table_id:
        table_id = client.ensure_table(target["path"], target["name"])
        checkpoint["table_id"] = table_id
        save_checkpoint(checkpoint_path, checkpoint)
    processed = int(checkpoint.get("processed") or 0)
    start = processed
    snapshot = manifest.get("snapshot")
    shard_indexes = set(range(shard_index, shard_count + 1, shard_count))
    for index, entry in enumerate(iter_entries(bundle_path), start=1):
        if index <= start:
            continue
        if limit is not None and processed - start >= limit:
            break
        if shard_count > 1 and ((index - 1) % shard_count) + 1 not in shard_indexes:
            if index % 5000 == 0:
                checkpoint["processed"] = index
                save_checkpoint(checkpoint_path, checkpoint)
            continue
        client.create_row(table_id, row_values(entry, snapshot), f"oeis:{entry['a_number']}")
        processed = index
        checkpoint["processed"] = processed
        save_checkpoint(checkpoint_path, checkpoint)
    return checkpoint


def audit_bundle(client, bundle_path, manifest, table_id, max_rows=None):
    """Compare imported rows with the verified bundle (paginated, read-only)."""
    expected = {entry["a_number"]: entry["terms"] for entry in iter_entries(bundle_path)}
    seen = set()
    mismatched = []
    extra = []
    scanned = 0
    cursor = ""
    complete = False
    while True:
        rows, cursor = client.list_rows(table_id, cursor)
        for row in rows:
            values = row.get("values") or {}
            a_number = values.get("a_number")
            if not isinstance(a_number, str) or a_number not in expected:
                extra.append(a_number)
                continue
            if a_number in seen:
                extra.append(a_number)
                continue
            if values.get("terms") != expected[a_number]:
                mismatched.append(a_number)
            seen.add(a_number)
        scanned += len(rows)
        if not cursor:
            complete = True
            break
        if max_rows is not None and scanned >= max_rows:
            break
    missing = sorted(set(expected) - seen)
    return {
        "scanned": scanned,
        "expected": len(expected),
        "seen": len(seen),
        "missing_count": len(missing),
        "mismatched_count": len(mismatched),
        "extra_count": len(extra),
        "missing": missing[:100],
        "mismatched": sorted(mismatched)[:100],
        "extra": extra[:100],
        "complete": complete,
    }


def read_token():
    token = os.getenv(TOKEN_ENV)
    if token:
        return token.strip()
    token_file = os.getenv(TOKEN_FILE_ENV)
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    return ""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, help="bundle JSONL produced by build_oeis_bundle.py")
    parser.add_argument("--manifest", default=None, help="manifest path (default: <bundle>.manifest.json)")
    parser.add_argument("--base-url", default=os.getenv(URL_ENV, ""), help="XeLibrary base URL")
    parser.add_argument(
        "--ca-file",
        default=os.getenv("XELIBRARY_CA_FILE", ""),
        help="CA bundle for a private LAN HTTPS endpoint (XELIBRARY_CA_FILE)",
    )
    parser.add_argument("--path", required=True, help="target logical path, e.g. /OEIS")
    parser.add_argument("--name", required=True, help="target table name")
    parser.add_argument("--checkpoint", required=True, help="resume checkpoint JSON (0600)")
    parser.add_argument("--limit", type=int, default=None, help="import at most N rows this run")
    parser.add_argument("--audit", action="store_true", help="only verify imported rows against the bundle")
    parser.add_argument("--audit-limit", type=int, default=None, help="stop after scanning N rows")
    parser.add_argument("--shard-count", type=int, default=1, help="split the bundle across N importers")
    parser.add_argument("--shard-index", type=int, default=1, help="this importer's 1-based shard index")
    parser.add_argument(
        "--retry-429",
        type=int,
        default=5,
        help="retry a rate-limited request this many times, honouring Retry-After",
    )
    args = parser.parse_args(argv)
    if not args.base_url:
        parser.error("--base-url or XELIBRARY_URL is required")
    token = read_token()
    if not token:
        parser.error(f"{TOKEN_ENV} or {TOKEN_FILE_ENV} is required")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.audit_limit is not None and args.audit_limit < 1:
        parser.error("--audit-limit must be positive")
    if args.retry_429 < 0:
        parser.error("--retry-429 must not be negative")
    if args.shard_count < 1 or not 1 <= args.shard_index <= args.shard_count:
        parser.error("--shard-index must be within 1..--shard-count")
    target = {"path": args.path, "name": args.name}
    try:
        manifest = verify_bundle(args.bundle, args.manifest)
        client = APIClient(
            args.base_url,
            token,
            retry_429=args.retry_429,
            ca_file=args.ca_file or None,
        )
        if args.audit:
            checkpoint = load_checkpoint(args.checkpoint, manifest["sha256"], target)
            if checkpoint is None or not checkpoint.get("table_id"):
                raise ImportError_("audit requires an existing checkpoint with a table_id")
            report = audit_bundle(
                client, args.bundle, manifest, checkpoint["table_id"], max_rows=args.audit_limit
            )
            print(json.dumps({"ok": report["complete"] and not report["missing_count"] and not report["mismatched_count"] and not report["extra_count"], "table_id": checkpoint["table_id"], **report}, ensure_ascii=False, sort_keys=True))
            return 0 if not report["missing_count"] and not report["mismatched_count"] and not report["extra_count"] else 1
        checkpoint = import_bundle(
            client, args.bundle, manifest, target, args.checkpoint, limit=args.limit
        )
    except ImportError_ as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "table_id": checkpoint["table_id"],
                "processed": checkpoint["processed"],
                "total": manifest["count"],
                "snapshot": manifest.get("snapshot"),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
