"""Local process-level failure injection for crash-consistency evidence.

plan.md §79 requires crash recovery for .xl writes and §100 task 30 requires a
failure-injection test. Unit tests already cover claim fencing, job
interruption accounting and expired-lease freezing; this script adds real
SIGKILL evidence against local uvicorn processes:

1. transaction boundary: SIGKILL before COMMIT leaves no partial state;
2. XeLibrary: concurrent row writes, SIGKILL, restart on the same data dir ->
   SQLite integrity ok, every stored row is complete, new writes succeed;
3. DevAgent: work claim fencing and webhook delivery dedup survive SIGKILL.

Everything runs in temporary directories against 127.0.0.1 and never touches
the LAN deployment, PVE or production.
"""

import hashlib
import hmac
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx

ROOT = Path(__file__).resolve().parents[1]
WEBHOOK_SECRET = "failure-injection-secret"
TOKENS = {
    "alice": {
        "sub": "alice",
        "scope": "dev:read dev:write xelibrary:read xelibrary:write xelibrary:query",
        "agent_kind": "codex",
    },
    "bob": {
        "sub": "bob",
        "scope": "dev:read dev:write xelibrary:read xelibrary:write xelibrary:query",
        "agent_kind": "opencode",
    },
    "admin": {
        "sub": "admin",
        "scope": "xelibrary:read xelibrary:write xelibrary:admin",
        "agent_kind": "human",
    },
}
ALICE = {"Authorization": "Bearer alice"}
BOB = {"Authorization": "Bearer bob"}
ADMIN = {"Authorization": "Bearer admin"}


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def start_service(service, data_dir):
    port = free_port()
    environment = {
        **os.environ,
        "SERVICE": service,
        "DATA_DIR": str(data_dir),
        "APP_MODE": "development",
        "DEV_TOKENS_JSON": json.dumps(TOKENS),
        "FORGEJO_WEBHOOK_SECRET": WEBHOOK_SECRET,
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "xeonolab.services:create_app",
            "--factory",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(80):
        if process.poll() is not None:
            raise RuntimeError(f"{service} exited during startup")
        try:
            health = httpx.get(f"http://127.0.0.1:{port}/health/live", timeout=1, trust_env=False)
            if health.status_code == 200:
                return process, port
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError(f"{service} did not become ready")


def sigkill(process):
    if process.poll() is None:
        process.send_signal(signal.SIGKILL)
    process.wait(timeout=10)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def integrity_ok(database):
    connection = sqlite3.connect(database, timeout=10)
    try:
        return connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        connection.close()


def scenario_transaction_boundary(root):
    database = root / "boundary.sqlite"
    child_code = (
        "import sys, time\n"
        "from xeonolab.common import transaction\n"
        "with transaction(sys.argv[1]) as db:\n"
        "    db.execute('CREATE TABLE marker(value INTEGER)')\n"
        "    db.execute('INSERT INTO marker VALUES (1)')\n"
        "with transaction(sys.argv[1]) as db:\n"
        "    db.execute('INSERT INTO marker VALUES (2)')\n"
        "    time.sleep(60)\n"
    )
    child = subprocess.Popen([sys.executable, "-c", child_code, str(database)], cwd=ROOT)
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            if database.exists():
                try:
                    connection = sqlite3.connect(database, timeout=1)
                    count = connection.execute("SELECT count(*) FROM marker").fetchone()[0]
                    connection.close()
                    if count == 1:
                        break
                except sqlite3.Error:
                    pass
            time.sleep(0.1)
        else:
            raise AssertionError("first commit never became visible")
        sigkill(child)
        assert child.returncode == -signal.SIGKILL, child.returncode
        assert integrity_ok(database)
        connection = sqlite3.connect(database, timeout=10)
        rows = connection.execute("SELECT value FROM marker").fetchall()
        connection.close()
        assert rows == [(1,)], rows
        print("PASS transaction boundary: uncommitted transaction invisible after SIGKILL")
    finally:
        stop(child)


def scenario_library_crash(root):
    data = root / "xelibrary"
    process, port = start_service("xelibrary", data)
    base = f"http://127.0.0.1:{port}"
    try:
        created = httpx.post(
            base + "/api/v1/tables",
            headers=ALICE,
            timeout=10,
            json={
                "path": "/injection/data",
                "name": "injection",
                "columns": [
                    {"name": "marker", "type": "int64", "nullable": False},
                    {"name": "title", "type": "string", "nullable": False},
                ],
            },
        )
        created.raise_for_status()
        table_id = created.json()["data"]["table_id"]
        stop_writing = threading.Event()
        sent, lock = [], threading.Lock()

        def writer():
            client = httpx.Client(timeout=5, trust_env=False)
            while not stop_writing.is_set():
                with lock:
                    value = len(sent)
                try:
                    result = client.post(
                        f"{base}/api/v1/tables/{table_id}/rows",
                        headers=ALICE,
                        json={"values": {"marker": value, "title": f"t{value}"}},
                    )
                    if result.status_code == 201:
                        with lock:
                            sent.append(value)
                except httpx.HTTPError:
                    pass
            client.close()

        threads = [threading.Thread(target=writer, daemon=True) for _ in range(4)]
        for thread in threads:
            thread.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            with lock:
                if len(sent) >= 20:
                    break
            time.sleep(0.05)
        with lock:
            committed_before_kill = len(sent)
        assert committed_before_kill >= 20, committed_before_kill
        sigkill(process)
        stop_writing.set()
        for thread in threads:
            thread.join(timeout=5)

        process, port = start_service("xelibrary", data)
        base = f"http://127.0.0.1:{port}"
        for database in [data / "catalogue.sqlite", *sorted((data / "tables").glob("*.xl"))]:
            assert integrity_ok(database), database
        result = httpx.post(
            base + "/api/v1/query",
            headers=ALICE,
            timeout=10,
            json={"expression": 'FROM "/injection/**" LIMIT 1000'},
        )
        result.raise_for_status()
        rows = result.json()["data"]
        assert len(rows) >= committed_before_kill, (len(rows), committed_before_kill)
        for row in rows:
            assert isinstance(row["values"]["marker"], int)
            assert row["values"]["title"] == f"t{row['values']['marker']}"
        follow_up = httpx.post(
            f"{base}/api/v1/tables/{table_id}/rows",
            headers=ALICE,
            timeout=10,
            json={"values": {"marker": 10**6, "title": "after-crash"}},
        )
        assert follow_up.status_code == 201, follow_up.text
        print(
            f"PASS xelibrary crash: {len(rows)} rows intact after SIGKILL, "
            "catalogue/.xl integrity ok, writes resume"
        )
    finally:
        stop(process)


def scenario_collaboration_crash(root):
    data = root / "dev-agent"
    process, port = start_service("dev-agent", data)
    base = f"http://127.0.0.1:{port}"
    try:
        created = httpx.post(
            base + "/api/v1/work",
            headers=ALICE,
            timeout=10,
            json={"repository": "xenon-admin/XeonoLab", "issue_id": 9001, "title": "Injection task"},
        )
        created.raise_for_status()
        work_id = created.json()["data"]["id"]
        claimed = httpx.post(
            f"{base}/api/v1/work/{work_id}/claim", headers=ALICE, json={"ttl_seconds": 3600}
        )
        assert claimed.status_code == 200, claimed.text
        assert claimed.json()["data"]["epoch"] == 1
        raw = json.dumps(
            {
                "repository": {"full_name": "xenon-admin/XeonoLab"},
                "issue": {
                    "number": 9002,
                    "title": "Webhook task",
                    "state": "open",
                    "updated_at": "2026-09-30T12:00:00Z",
                },
            }
        ).encode()
        delivery_headers = {
            "X-Forgejo-Delivery": "injection-1",
            "X-Forgejo-Event": "issues",
            "X-Forgejo-Signature": hmac.new(WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest(),
        }
        first = httpx.post(
            base + "/api/v1/webhooks/forgejo", content=raw, headers=delivery_headers, timeout=10
        )
        assert first.status_code == 200, first.text
        assert first.json()["data"]["duplicate"] is False

        sigkill(process)
        process, port = start_service("dev-agent", data)
        base = f"http://127.0.0.1:{port}"
        items = httpx.get(base + "/api/v1/work?limit=100", headers=ALICE, timeout=10).json()["data"]
        item = next(entry for entry in items if entry["id"] == work_id)
        assert item["holder"] == "alice" and item["epoch"] == 1 and item["claim_active"] is True
        assert httpx.post(f"{base}/api/v1/work/{work_id}/claim", headers=BOB, json={}).status_code == 409
        released = httpx.post(
            f"{base}/api/v1/work/{work_id}/release", headers=ALICE, json={"epoch": 1}
        )
        assert released.status_code == 200, released.text
        second = httpx.post(
            base + "/api/v1/webhooks/forgejo", content=raw, headers=delivery_headers, timeout=10
        )
        assert second.status_code == 200, second.text
        assert second.json()["data"]["duplicate"] is True
        print("PASS collaboration crash: claim fence and webhook delivery dedup survive SIGKILL")
    finally:
        stop(process)


def create_table_via_api(base, headers, name, path):
    response = httpx.post(
        base + "/api/v1/tables",
        headers=headers,
        timeout=10,
        json={"path": path, "name": name, "columns": [{"name": "title", "type": "string"}]},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]["table_id"]


def add_pending_operation(data, kind, table_id, payload="{}"):
    with sqlite3.connect(data / "catalogue.sqlite", timeout=10) as database:
        stamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        database.execute(
            "INSERT INTO operations VALUES(?,?,?,?,?,?,?,NULL)",
            (str(uuid4()), kind, table_id, "pending", payload, stamp, stamp),
        )


def scenario_lifecycle_recovery(root):
    """Restart the real XeLibrary service on simulated crash leftovers (Issue #81)."""
    data = root / "lifecycle"
    process, port = start_service("xelibrary", data)
    base = f"http://127.0.0.1:{port}"
    try:
        rollback_id = create_table_via_api(base, ALICE, "删除回滚", "/源/delete-rollback")
        published_id = create_table_via_api(base, ALICE, "已发布", "/源/published")
        move_id = create_table_via_api(base, ALICE, "移动对账", "/源/move")
    finally:
        stop(process)

    tables = data / "tables"
    # 1. interrupted delete that must roll back (catalogue row still present).
    (tables / f"{rollback_id}.xl").rename(tables / f".{rollback_id}.xl.deleting")
    add_pending_operation(data, "table.delete", rollback_id)
    # 2. interrupted create whose file was never published to the catalogue.
    unpublished_id = str(uuid4())
    shutil.copyfile(tables / f"{published_id}.xl", tables / f"{unpublished_id}.xl")
    with sqlite3.connect(tables / f"{unpublished_id}.xl", timeout=10) as database:
        database.execute("UPDATE __xl_meta SET table_id=?", (unpublished_id,))
    add_pending_operation(data, "table.create", unpublished_id, '{"path": "/源/unpublished"}')
    # 3. committed move whose table metadata path lags the catalogue.
    with sqlite3.connect(data / "catalogue.sqlite", timeout=10) as database:
        database.execute("UPDATE tables SET path=? WHERE id=?", ("/已移动", move_id))
    add_pending_operation(
        data, "table.move", move_id, '{"from": "/源/move", "to": "/已移动"}'
    )

    process, port = start_service("xelibrary", data)
    base = f"http://127.0.0.1:{port}"
    try:
        for table_id in (rollback_id, published_id, move_id):
            detail = httpx.get(base + f"/api/v1/tables/{table_id}", headers=ADMIN, timeout=10)
            assert detail.status_code == 200, detail.text
        assert (tables / f"{rollback_id}.xl").is_file(), "delete rollback did not restore the table file"
        assert not (tables / f"{unpublished_id}.xl").exists(), "unpublished create file was not removed"
        moved = httpx.get(base + f"/api/v1/tables/{move_id}/schema", headers=ADMIN, timeout=10)
        assert moved.status_code == 200, moved.text
        assert moved.json()["data"]["path"] == "/已移动"
        with sqlite3.connect(data / "catalogue.sqlite", timeout=10) as database:
            states = dict(database.execute("SELECT table_id,state FROM operations"))
            recovered = database.execute(
                "SELECT count(*) FROM audit WHERE event='operation.recovered'"
            ).fetchone()[0]
        assert states[rollback_id] == "aborted", states
        assert states[unpublished_id] == "completed", states
        assert states[move_id] == "completed", states
        assert recovered >= 3, recovered
        print("PASS lifecycle recovery: delete rollback, unpublished-create cleanup and move reconcile after restart")
    finally:
        stop(process)


def main():
    failures = 0
    with tempfile.TemporaryDirectory(prefix="failure-injection-") as temporary:
        root = Path(temporary)
        for scenario in (scenario_transaction_boundary, scenario_library_crash, scenario_lifecycle_recovery, scenario_collaboration_crash):
            try:
                scenario(root)
            except Exception:
                failures += 1
                print(f"FAIL {scenario.__name__}")
                traceback.print_exc()
    if failures:
        raise SystemExit(f"{failures} failure-injection scenario(s) failed")
    print("PASS failure injection: all scenarios")


if __name__ == "__main__":
    main()
