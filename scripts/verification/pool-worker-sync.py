"""Loopback-only observation of the receiver test's worker-completion boundary."""
import argparse
import hashlib
import inspect
import json
import linecache
import subprocess
import tempfile
import threading
import traceback
import types
from pathlib import Path
from socketserver import ThreadingMixIn

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASELINE = "865a72277008a5773236b50b993bacdecc78d6ab"
parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=("before", "current"), default="current")
parser.add_argument("--repeat", type=int, default=10)
args = parser.parse_args()
if not 1 <= args.repeat <= 30:
    parser.error("repeat must be between 1 and 30")
source = (
    subprocess.check_output(["git", "show", f"{BASELINE}:tests/test_pool_telemetry.py"], cwd=ROOT)
    if args.mode == "before" else (ROOT / "tests/test_pool_telemetry.py").read_bytes()
)
module = types.ModuleType("receiver_sync_observation")
module.__file__ = (
    "<865a722:tests/test_pool_telemetry.py>" if args.mode == "before"
    else str(ROOT / "tests/test_pool_telemetry.py")
)
linecache.cache[module.__file__] = (len(source), None, source.decode().splitlines(keepends=True), module.__file__)
exec(compile(source, module.__file__, "exec"), module.__dict__)
receiver_origin = Path(inspect.getfile(module.Receiver)).resolve()
assert receiver_origin == ROOT / "infra/hfcache/pool_metrics_receiver.py", receiver_origin
original = module.Receiver.process_request_thread
entered, release, completed = threading.Event(), threading.Event(), threading.Event()


def paused_cleanup(self, request, address):
    try:
        ThreadingMixIn.process_request_thread(self, request, address)
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("controlled cleanup release was not signaled")
    finally:
        self._request_slots.release()
        completed.set()


results = []
try:
    if args.mode == "before":
        # Exact baseline test and unchanged product; test-only controlled scheduling.
        module.Receiver.process_request_thread = paused_cleanup
    for index in range(args.repeat if args.mode == "current" else 1):
        monkeypatch = pytest.MonkeyPatch()
        with tempfile.TemporaryDirectory(prefix="receiver-sync-") as directory:
            try:
                params = [Path(directory)]
                if args.mode == "current":
                    params.append(monkeypatch)
                module.test_http_receiver_bounds_slow_clients_and_concurrent_requests(*params)
                results.append({"iteration": index + 1, "outcome": "pass"})
            except AssertionError as error:
                cursor = error.__traceback__
                while cursor.tb_next:
                    cursor = cursor.tb_next
                results.append({"iteration": index + 1, "outcome": "assertion", "trace": traceback.format_exc(),
                                "firstClientResponse": cursor.tb_frame.f_locals.get("first_result"),
                                "replyHex": cursor.tb_frame.f_locals.get("reply", b"").hex()})
                if args.mode != "before":
                    raise
            finally:
                release.set()
                monkeypatch.undo()
    if args.mode == "before":
        assert entered.is_set() and completed.wait(timeout=2)
        assert results[0]["outcome"] == "assertion"
        assert 'assert b"408" in reply' in results[0]["trace"]
        assert results[0]["firstClientResponse"] == [204] and results[0]["replyHex"] == ""
finally:
    release.set()
    module.Receiver.process_request_thread = original
report = {"mode": args.mode, "baseline": BASELINE, "results": results,
          "testSourceHash": hashlib.sha256(source).hexdigest(),
          "receiverModulePath": str(receiver_origin),
          "receiverSourceHash": hashlib.sha256((ROOT / "infra/hfcache/pool_metrics_receiver.py").read_bytes()).hexdigest(),
          "limits": "Isolated loopback temporary files and controlled scheduling; not proof of the external deployment's unique failure cause. No product receiver modifications or production access."}
out = ROOT / "runtime/verification/pool-worker-sync"
out.mkdir(parents=True, exist_ok=True)
(out / f"{args.mode}.json").write_text(json.dumps(report, indent=2) + "\n")
print(json.dumps(report, indent=2))
