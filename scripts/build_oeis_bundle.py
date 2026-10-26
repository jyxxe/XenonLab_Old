#!/usr/bin/env python3
"""Build an offline, verifiable OEIS bundle from the official stripped data file.

The importer (separate increment) consumes this bundle instead of the network, so
production imports are reproducible and can be audited by digest. The official
``https://oeis.org/stripped.gz`` format is one sequence per line::

    # OEIS Sequence Data (http://oeis.org/stripped.gz)
    # Last Modified: October 25 04:28 UTC 2026
    A000001 ,0,1,1,1,2,1,...

This builder keeps the exact A-number and decimal term string, rejects malformed
or unsorted input, and publishes ``<output>`` plus ``<output>.manifest.json``
atomically (temporary file + fsync + rename). It refuses to overwrite an
existing bundle so a snapshot cannot be silently replaced.
"""

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BUNDLE_FORMAT = "oeis-stripped-v1"
DEFAULT_SOURCE = "https://oeis.org/stripped.gz"
A_NUMBER = re.compile(r"A\d{6}")
LINE = re.compile(r"^(A\d{6})\s*,(.*)$")
SNAPSHOT = re.compile(r"^#\s*Last Modified:\s*(.+?)\s*$")
TERMS = re.compile(r"[-0-9,\s]+")
MAX_LINE_CHARS = 1_000_000


class BundleError(ValueError):
    """Rejected input or unsafe output state."""


def term_count(terms):
    return 0 if not terms.strip() else len([item for item in terms.split(",") if item.strip()])


def parse_stripped(lines):
    """Return ``(snapshot, entries)`` from the stripped-format lines."""
    snapshot = None
    entries = []
    previous = None
    for number, raw in enumerate(lines, start=1):
        line = raw.rstrip("\r\n")
        if line.startswith("#"):
            match = SNAPSHOT.match(line)
            if match:
                snapshot = match.group(1)
            continue
        if not line.strip():
            continue
        if len(line) > MAX_LINE_CHARS:
            raise BundleError(f"line {number}: entry exceeds {MAX_LINE_CHARS} characters")
        match = LINE.match(line)
        if not match:
            raise BundleError(f"line {number}: expected 'Axxxxxx ,<terms>'")
        a_number, terms = match.group(1), match.group(2).strip()
        if not A_NUMBER.fullmatch(a_number):
            raise BundleError(f"line {number}: invalid A-number {a_number!r}")
        if terms and not TERMS.fullmatch(terms):
            raise BundleError(f"line {number}: terms contain unsupported characters")
        key = int(a_number[1:])
        if previous is not None and key <= previous:
            raise BundleError(f"line {number}: duplicate or unsorted A-number {a_number}")
        previous = key
        entries.append({"a_number": a_number, "terms": terms})
    return snapshot, entries


def read_input(path):
    with open(path, "rb") as raw:
        magic = raw.read(2)
    if magic == b"\x1f\x8b" or str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return open(path, "rt", encoding="utf-8", newline="")


def _atomic_write(path, writer):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as output:
            writer(output)
            output.flush()
            os.fsync(output.fileno())
        return Path(temporary)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def build(input_path, output_path, *, source=DEFAULT_SOURCE, snapshot=None, limit=None):
    """Write the bundle and manifest; never overwrites an existing snapshot."""
    output_path = Path(output_path)
    manifest_path = output_path.with_name(output_path.name + ".manifest.json")
    if output_path.exists() or manifest_path.exists():
        raise BundleError(f"refusing to overwrite existing bundle {output_path}")

    digest = hashlib.sha256()
    count = 0
    terms_total = 0
    observed_snapshot = snapshot

    def write_bundle(handle):
        nonlocal count, terms_total, observed_snapshot
        with read_input(input_path) as stream:
            parsed_snapshot, entries = parse_stripped(stream)
        if observed_snapshot is None:
            observed_snapshot = parsed_snapshot
        for entry in entries:
            if limit is not None and count >= limit:
                break
            encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n"
            digest.update(encoded)
            handle.write(encoded)
            count += 1
            terms_total += term_count(entry["terms"])

    temporary = _atomic_write(output_path, write_bundle)
    try:
        bundle_bytes = temporary.stat().st_size
        os.replace(temporary, output_path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    manifest = {
        "format": BUNDLE_FORMAT,
        "source": source,
        "snapshot": observed_snapshot,
        "count": count,
        "term_count": terms_total,
        "bytes": bundle_bytes,
        "sha256": digest.hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "limit": limit,
    }

    def write_manifest(handle):
        handle.write(json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8") + b"\n")

    temporary_manifest = _atomic_write(manifest_path, write_manifest)
    try:
        os.replace(temporary_manifest, manifest_path)
    except BaseException:
        temporary_manifest.unlink(missing_ok=True)
        raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, help="official stripped file (.txt or .gz) or '-' for stdin")
    parser.add_argument("--output", required=True, help="bundle path (JSONL, must not exist)")
    parser.add_argument("--source", default=DEFAULT_SOURCE, help="source URL recorded in the manifest")
    parser.add_argument("--snapshot", default=None, help="override snapshot label (defaults to file header)")
    parser.add_argument("--limit", type=int, default=None, help="stop after N sequences (smoke tests)")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.input == "-":
        with tempfile.NamedTemporaryFile("wb", suffix=".stripped", delete=False) as buffer:
            buffer.write(sys.stdin.buffer.read())
            temporary_input = buffer.name
        try:
            manifest = build(
                temporary_input,
                args.output,
                source=args.source,
                snapshot=args.snapshot,
                limit=args.limit,
            )
        finally:
            Path(temporary_input).unlink(missing_ok=True)
    else:
        manifest = build(
            args.input, args.output, source=args.source, snapshot=args.snapshot, limit=args.limit
        )
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
