import hashlib
import hmac
import http.client
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from infra.hfcache.pool_metrics_receiver import (
    PATH,
    PoolMetricsStore,
    Receiver,
    Reject,
    handler_for,
    signing_bytes,
)
from infra.pve.pool_telemetry_collector import CollectorError, collect, signed_headers

POOL = "physical-pool-from-deployment-config"
SECRET = b"test-only-hmac-key-with-at-least-32-bytes"
NOW = int(time.time())


def observed_at(timestamp=NOW):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sample(timestamp=NOW, **overrides):
    payload = {
        "pool": POOL,
        "total": 1000,
        "used": 250,
        "free": 750,
        "observed_at": observed_at(timestamp),
    }
    payload.update(overrides)
    return payload


def encoded(payload):
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def authorized_request(payload, secret=SECRET, timestamp=None):
    body = encoded(payload)
    timestamp_text = str(payload["observed_at_epoch"]) if "observed_at_epoch" in payload else str(
        int(datetime.strptime(payload["observed_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp())
        if timestamp is None
        else timestamp
    )
    signature = "v1=" + hmac.new(secret, signing_bytes(timestamp_text, body), hashlib.sha256).hexdigest()
    return body, timestamp_text, signature


def store_at(path, clock=lambda: NOW):
    return PoolMetricsStore(path, SECRET, frozenset({POOL}), clock=clock)


def test_collector_uses_only_fixed_zpool_argv_and_emits_byte_contract():
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return type("Result", (), {"stdout": f"{POOL}\t1000\t250\t750\n"})()

    result = collect(POOL, run=fake_run)
    assert calls == [
        (
            ["/sbin/zpool", "list", "-H", "-p", "-o", "name,size,alloc,free", POOL],
            {"check": True, "capture_output": True, "text": True, "timeout": 10, "shell": False},
        )
    ]
    assert result["pool"] == POOL
    assert result["total"] == 1000 and result["used"] == 250 and result["free"] == 750
    assert set(result) == {"pool", "total", "used", "free", "observed_at"}


@pytest.mark.parametrize(
    "output",
    [
        "",
        "pool\t1\t1\t0\nextra\t1\t1\t0\n",
        "other\t1000\t250\t750\n",
        f"{POOL}\t1000\t250\t749\n",
        f"{POOL}\tNaN\t250\t750\n",
    ],
)
def test_collector_rejects_unexpected_zpool_output(output):
    def fake_run(_argv, **_kwargs):
        return type("Result", (), {"stdout": output})()

    with pytest.raises(CollectorError):
        collect(POOL, run=fake_run)


def test_receiver_rejects_malformed_expired_wrong_pool_and_partial_updates(tmp_path):
    path = tmp_path / "physical-pool.json"
    store = store_at(path)
    initial = sample(NOW - 10)
    body, timestamp, signature = authorized_request(initial)
    store.accept(body, timestamp, signature)
    before = path.read_bytes()

    invalid = [
        b"{broken",
        b'{"pool":"' + POOL.encode() + b'","pool":"' + POOL.encode() + b'","total":1000,"used":250,"free":750,"observed_at":"' + observed_at(NOW).encode() + b'"}',
        encoded({**sample(NOW), "unexpected": "field"}),
        encoded(sample(NOW, pool="other-pool")),
        encoded(sample(NOW, used=True)),
        encoded(sample(NOW, free=749)),
        encoded(sample(NOW, observed_at="not-a-timestamp")),
    ]
    for bad_body in invalid:
        # The HMAC is valid so each case reaches schema/data validation.
        ts = str(NOW)
        sig = "v1=" + hmac.new(SECRET, signing_bytes(ts, bad_body), hashlib.sha256).hexdigest()
        with pytest.raises(Reject):
            store.accept(bad_body, ts, sig)
    expired = sample(NOW - 121)
    expired_body, expired_time, expired_sig = authorized_request(expired)
    with pytest.raises(Reject):
        store.accept(expired_body, expired_time, expired_sig)
    assert path.read_bytes() == before


def test_receiver_rejects_bad_hmac_and_oversized_body(tmp_path):
    store = store_at(tmp_path / "pool.json")
    body, timestamp, _ = authorized_request(sample())
    with pytest.raises(Reject, match="signature"):
        store.accept(body, timestamp, "v1=" + "0" * 64)
    with pytest.raises(Reject, match="large"):
        store.accept(b"x" * 4097, timestamp, "bad")


def test_receiver_rejects_replay_after_restart_and_keeps_stale_sample(tmp_path):
    path = tmp_path / "pool.json"
    old = sample(NOW - 500)
    path.write_bytes(encoded(old) + b"\n")
    path.chmod(0o644)
    before = path.read_bytes()
    first_receiver_after_restart = store_at(path)
    expired = sample(NOW - 121)
    body, timestamp, signature = authorized_request(expired)
    with pytest.raises(Reject):
        first_receiver_after_restart.accept(body, timestamp, signature)
    assert path.read_bytes() == before

    fresh = sample(NOW)
    body, timestamp, signature = authorized_request(fresh)
    store_at(path).accept(body, timestamp, signature)
    assert json.loads(path.read_text()) == fresh
    # The older sample can never replace the durable newer timestamp.
    older = sample(NOW - 1)
    body, timestamp, signature = authorized_request(older)
    with pytest.raises(Reject):
        store_at(path).accept(body, timestamp, signature)


def test_simultaneous_updates_serialize_and_only_publish_one_sample(tmp_path):
    path = tmp_path / "pool.json"
    store = store_at(path)
    payload = sample()
    body, timestamp, signature = authorized_request(payload)
    barrier = threading.Barrier(12)

    def update():
        barrier.wait()
        try:
            store.accept(body, timestamp, signature)
            return "accepted"
        except Reject:
            return "rejected"

    with ThreadPoolExecutor(max_workers=12) as pool:
        outcomes = list(pool.map(lambda _: update(), range(12)))
    assert outcomes.count("accepted") == 1
    assert outcomes.count("rejected") == 11
    assert json.loads(path.read_text()) == payload
    assert path.stat().st_mode & 0o022 == 0


def test_http_receiver_does_not_log_credentials_or_request_content(tmp_path, capsys):
    path = tmp_path / "pool.json"
    store = store_at(path)
    server = Receiver(("127.0.0.1", 0), handler_for(store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    body = encoded(sample())
    try:
        connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
        connection.request(
            "POST",
            PATH,
            body=body,
            headers={
                **signed_headers(body, SECRET, NOW),
                "X-XeonoLab-Signature": "v1=" + "f" * 64,
                "X-Test-Secret": "credential-that-must-not-be-logged",
            },
        )
        response = connection.getresponse()
        assert response.status == 401
        response.read()
        connection.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    captured = capsys.readouterr()
    assert "credential-that-must-not-be-logged" not in captured.out + captured.err
    assert SECRET.decode() not in captured.out + captured.err
    assert not path.exists()


def test_receiver_refuses_group_or_world_writable_target(tmp_path):
    path = tmp_path / "pool.json"
    path.write_text(encoded(sample()).decode())
    path.chmod(0o666)
    body, timestamp, signature = authorized_request(sample())
    with pytest.raises(RuntimeError, match="group/world writable"):
        store_at(path).accept(body, timestamp, signature)
