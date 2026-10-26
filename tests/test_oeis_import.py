"""Issue #261 second increment: idempotent OEIS bundle importer."""

import json
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scripts.build_oeis_bundle import build
from scripts.import_oeis import (
    APIClient,
    ImportError_,
    import_bundle,
    iter_entries,
    row_values,
    save_checkpoint,
    verify_bundle,
)

SAMPLE = "# OEIS Sequence Data\n# Last Modified: October 25 04:28 UTC 2026\nA000001 ,0,1,1\nA000002 ,1,2\nA000003 ,5\n"
TARGET = {"path": "/OEIS", "name": "OEIS 数据"}


def make_bundle(tmp_path):
    source = tmp_path / "stripped.txt"
    source.write_text(SAMPLE, encoding="utf-8")
    bundle = tmp_path / "bundle.jsonl"
    manifest = build(source, bundle)
    return bundle, manifest


class FakeClient:
    def __init__(self, fail_at=None):
        self.rows = []
        self.fail_at = fail_at
        self.ensured = None

    def ensure_table(self, path, name):
        self.ensured = (path, name)
        return "table-1"

    def create_row(self, table_id, values, idempotency_key):
        if self.fail_at is not None and len(self.rows) == self.fail_at:
            raise ImportError_("simulated 401")
        self.rows.append((table_id, values, idempotency_key))
        return f"row-{len(self.rows)}"


def test_verify_bundle_rejects_tampering(tmp_path):
    bundle, manifest = make_bundle(tmp_path)
    verify_bundle(bundle)
    bundle.write_bytes(bundle.read_bytes() + b'{"a_number":"A999999","terms":""}\n')
    with pytest.raises(ImportError_, match="sha256|bytes|count"):
        verify_bundle(bundle)


def test_import_writes_rows_and_checkpoint(tmp_path):
    bundle, manifest = make_bundle(tmp_path)
    client = FakeClient()
    checkpoint_path = tmp_path / "state" / "oeis.checkpoint.json"
    checkpoint = import_bundle(client, bundle, verify_bundle(bundle), TARGET, checkpoint_path)
    assert client.ensured == ("/OEIS", "OEIS 数据")
    assert [row[2] for row in client.rows] == ["oeis:A000001", "oeis:A000002", "oeis:A000003"]
    assert client.rows[0][1] == {"a_number": "A000001", "terms": "0,1,1", "term_count": 3, "snapshot": "October 25 04:28 UTC 2026"}
    assert checkpoint["table_id"] == "table-1"
    assert checkpoint["processed"] == 3
    stored = json.loads(checkpoint_path.read_text())
    assert stored == checkpoint
    assert stat.S_IMODE(checkpoint_path.stat().st_mode) == 0o600


def test_import_resumes_from_checkpoint_and_honours_limit(tmp_path):
    bundle, manifest = make_bundle(tmp_path)
    checkpoint_path = tmp_path / "oeis.checkpoint.json"
    initial = {
        "version": 1,
        "bundle_sha256": manifest["sha256"],
        "snapshot": manifest["snapshot"],
        "target": TARGET,
        "table_id": "table-existing",
        "processed": 1,
    }
    save_checkpoint(checkpoint_path, initial)
    client = FakeClient()
    checkpoint = import_bundle(client, bundle, manifest, TARGET, checkpoint_path, limit=1)
    assert client.ensured is None
    assert len(client.rows) == 1
    assert client.rows[0][2] == "oeis:A000002"
    assert checkpoint["processed"] == 2
    assert checkpoint["table_id"] == "table-existing"


def test_checkpoint_is_bound_to_bundle_and_target(tmp_path):
    bundle, manifest = make_bundle(tmp_path)
    checkpoint_path = tmp_path / "oeis.checkpoint.json"
    save_checkpoint(
        checkpoint_path,
        {"version": 1, "bundle_sha256": "other", "target": TARGET, "processed": 1, "table_id": "t"},
    )
    with pytest.raises(ImportError_, match="different bundle"):
        import_bundle(FakeClient(), bundle, manifest, TARGET, checkpoint_path)
    save_checkpoint(
        checkpoint_path,
        {"version": 1, "bundle_sha256": manifest["sha256"], "target": {"path": "/x"}, "processed": 1},
    )
    with pytest.raises(ImportError_, match="different target"):
        import_bundle(FakeClient(), bundle, manifest, TARGET, checkpoint_path)


def test_failed_row_preserves_progress(tmp_path):
    bundle, manifest = make_bundle(tmp_path)
    client = FakeClient(fail_at=2)
    checkpoint_path = tmp_path / "oeis.checkpoint.json"
    with pytest.raises(ImportError_, match="simulated 401"):
        import_bundle(client, bundle, manifest, TARGET, checkpoint_path)
    stored = json.loads(checkpoint_path.read_text())
    assert stored["processed"] == 2
    assert [row[2] for row in client.rows] == ["oeis:A000001", "oeis:A000002"]


def test_row_values_and_iter_entries(tmp_path):
    bundle, _ = make_bundle(tmp_path)
    entries = list(iter_entries(bundle))
    assert entries[2] == {"a_number": "A000003", "terms": "5"}
    assert row_values({"a_number": "A000003", "terms": "5"}, "snap")["term_count"] == 1
    assert row_values({"a_number": "A000004", "terms": ""}, None)["term_count"] == 0


def test_api_client_creates_table_and_forwards_idempotency_key():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            body = json.dumps({"data": []}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            if self.path == "/api/v1/tables":
                body = json.dumps({"data": {"table_id": "table-9"}}).encode()
                self.send_response(201)
            else:
                seen["key"] = self.headers.get("Idempotency-Key")
                seen["values"] = payload["values"]
                body = json.dumps({"data": {"_row_id": "row-9"}}).encode()
                self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        client = APIClient(f"http://127.0.0.1:{server.server_address[1]}", "token")
        assert client.ensure_table("/OEIS", "OEIS 数据") == "table-9"
        client.create_row("table-9", {"a_number": "A000001"}, "oeis:A000001")
        assert seen["key"] == "oeis:A000001"
        assert seen["values"] == {"a_number": "A000001"}
    finally:
        server.shutdown()
        server.server_close()
