"""Issue #261 first increment: offline OEIS bundle builder."""

import gzip
import hashlib
import json

import pytest

from scripts.build_oeis_bundle import BundleError, build, parse_stripped, term_count

SAMPLE = """# OEIS Sequence Data (http://oeis.org/stripped.gz)
# Last Modified: October 25 04:28 UTC 2026
# Use of this content is governed by the OEIS End-User License.
A000001 ,0,1,1,1,2,1
A000002 ,1,2,2,1
A000003 ,-1,0,1
"""


def write_stripped(path, text=SAMPLE):
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_stripped_keeps_snapshot_and_exact_terms():
    snapshot, entries = parse_stripped(SAMPLE.splitlines(keepends=True))
    assert snapshot == "October 25 04:28 UTC 2026"
    assert entries == [
        {"a_number": "A000001", "terms": "0,1,1,1,2,1"},
        {"a_number": "A000002", "terms": "1,2,2,1"},
        {"a_number": "A000003", "terms": "-1,0,1"},
    ]
    assert term_count("") == 0
    assert term_count("1, 2 ,3") == 3


def test_parse_rejects_malformed_duplicate_and_unsorted_entries():
    with pytest.raises(BundleError, match="expected"):
        parse_stripped(["<!DOCTYPE html>"])
    with pytest.raises(BundleError, match="expected"):
        parse_stripped(["A1 ,1"])
    with pytest.raises(BundleError, match="duplicate or unsorted"):
        parse_stripped(["A000002 ,1", "A000001 ,0"])
    with pytest.raises(BundleError, match="unsupported characters"):
        parse_stripped(["A000001 ,1,<b>"])


def test_build_writes_bundle_and_manifest_atomically(tmp_path):
    source = write_stripped(tmp_path / "stripped.txt")
    output = tmp_path / "bundle.jsonl"

    manifest = build(source, output)

    assert output.is_file()
    manifest_path = tmp_path / "bundle.jsonl.manifest.json"
    stored = json.loads(manifest_path.read_text())
    assert stored == manifest
    assert manifest["format"] == "oeis-stripped-v1"
    assert manifest["source"] == "https://oeis.org/stripped.gz"
    assert manifest["snapshot"] == "October 25 04:28 UTC 2026"
    assert manifest["count"] == 3
    assert manifest["term_count"] == 13
    assert manifest["bytes"] == output.stat().st_size
    assert manifest["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    lines = [json.loads(line) for line in output.read_text().splitlines()]
    assert lines[0] == {"a_number": "A000001", "terms": "0,1,1,1,2,1"}
    assert not list(tmp_path.glob("*.tmp"))


def test_build_reads_gzip_and_honours_limit(tmp_path):
    source = tmp_path / "stripped.txt.gz"
    with gzip.open(source, "wt", encoding="utf-8") as stream:
        stream.write(SAMPLE)
    manifest = build(source, tmp_path / "bundle.jsonl", limit=2)
    assert manifest["count"] == 2
    assert manifest["limit"] == 2
    assert manifest["term_count"] == 10


def test_build_refuses_overwrite_and_leaves_existing_bundle(tmp_path):
    source = write_stripped(tmp_path / "stripped.txt")
    output = tmp_path / "bundle.jsonl"
    build(source, output)
    original = output.read_bytes()
    with pytest.raises(BundleError, match="refusing to overwrite"):
        build(source, output)
    assert output.read_bytes() == original


def test_build_supports_empty_term_sets(tmp_path):
    source = write_stripped(tmp_path / "stripped.txt", "A000001 ,\nA000002 ,1\n")
    manifest = build(source, tmp_path / "bundle.jsonl")
    assert manifest["count"] == 2
    assert manifest["term_count"] == 1
