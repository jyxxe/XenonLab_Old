import json
import logging
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from xeonolab import hf_cache
from xeonolab.common import APIError, Identity, transaction

from .conftest import auth

REV = "a" * 40


def local_download(repo_type, repo_id, revision, destination, allow_patterns, ignore_patterns, max_bytes):
    assert revision in (REV, "b" * 40), "downloader must receive an immutable resolved commit SHA"
    destination.mkdir(parents=True)
    (destination / "config.json").write_text('{"model":"fixture"}')
    (destination / "weights.bin").write_bytes(b"local-test-weights")


def client(settings):
    return TestClient(
        hf_cache.create_app(
            settings,
            download_backend=local_download,
            revision_resolver=lambda _repo_type, _repo_id, revision: (
                REV if revision in ("main", REV) else "b" * 40
            ),
        )
    )


def ensure_payload(**overrides):
    return {
        "repo_type": "model",
        "repo_id": "org/repo",
        "revision": REV,
        "priority": "normal",
        "pin": False,
        **overrides,
    }


def test_revision_pinned_ensure_manifest_pin_and_integrity(settings):
    with client(settings) as api:
        result = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(revision="main")
        ).json()["data"]
        assert result["status"] == "ready"
        assert result["revision"] == REV
        assert result["requested_revision"] == "main"
        assert result["resolved_revision"] == REV
        assert len(result["files"]) == 2
        item_id = result["id"]
        folder = Path(settings.data_dir) / "snapshots" / item_id
        assert (folder / ".xeonolab-manifest.json").is_file()
        again = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(revision="main")).json()[
            "data"
        ]
        assert again["status"] == "cached" and again["id"] == item_id
        moved_tag = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(revision="tag-v2")
        ).json()["data"]
        assert moved_tag["resolved_revision"] == "b" * 40 and moved_tag["id"] != item_id
        assert api.post(f"/api/v1/cache/items/{item_id}/pin", headers=auth()).status_code == 200
        assert api.post(f"/api/v1/cache/items/{item_id}/evict", headers=auth()).status_code == 409
        assert api.delete(f"/api/v1/cache/items/{item_id}/pin", headers=auth()).status_code == 200
        (folder / "weights.bin").write_bytes(b"corrupted")
        corrupted = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        assert corrupted.status_code == 500
        assert corrupted.json()["error"]["code"] == "CACHE_INTEGRITY_FAILURE"


def test_active_lease_blocks_eviction_and_owner_controls_release(settings):
    with client(settings) as api:
        item_id = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]["id"]
        lease = api.post(f"/api/v1/cache/items/{item_id}/leases", headers=auth()).json()["data"]["lease_id"]
        assert api.post(f"/api/v1/cache/items/{item_id}/evict", headers=auth()).status_code == 409
        assert (
            api.delete(f"/api/v1/cache/items/{item_id}/leases/{lease}", headers=auth("bob")).status_code
            == 403
        )
        assert api.delete(f"/api/v1/cache/items/{item_id}/leases/{lease}", headers=auth()).status_code == 200
        assert api.post(f"/api/v1/cache/items/{item_id}/evict", headers=auth()).json()["data"]["evicted"]


def test_gc_only_removes_unpinned_unleased_items_and_storage_reports_watermark(settings, monkeypatch):
    with client(settings) as api:
        protected_id = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(pin=True)).json()[
            "data"
        ]["id"]
        leased_id = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/leased")
        ).json()["data"]["id"]
        lease = api.post(f"/api/v1/cache/items/{leased_id}/leases", headers=auth()).json()["data"]["lease_id"]
        evictable_id = api.post(
            "/api/v1/cache/ensure",
            headers=auth(),
            json=ensure_payload(repo_id="org/evictable", priority="low"),
        ).json()["data"]["id"]
        monkeypatch.setattr(
            hf_cache.shutil, "disk_usage", lambda _path: SimpleNamespace(total=100, used=95, free=5)
        )
        status = api.get("/api/v1/cache/storage", headers=auth()).json()["data"]
        assert status["watermark"] == "retain_protected"
        gc_result = api.post("/api/v1/cache/gc", headers=auth(), json={"target_usage_percent": 75}).json()[
            "data"
        ]
        assert gc_result["evicted"] == [evictable_id]
        assert protected_id not in gc_result["evicted"] and leased_id not in gc_result["evicted"]
        assert api.get(f"/api/v1/cache/items/{protected_id}", headers=auth()).status_code == 200
        assert api.get(f"/api/v1/cache/items/{leased_id}", headers=auth()).status_code == 200
        assert (
            api.delete(f"/api/v1/cache/items/{leased_id}/leases/{lease}", headers=auth()).status_code == 200
        )


def test_cache_endpoints_enforce_scopes(settings):
    settings.dev_tokens["limited"] = {"sub": "limited", "scope": "xelibrary:read", "agent_kind": "human"}
    with client(settings) as api:
        assert api.get("/api/v1/cache/items", headers=auth("limited")).status_code == 403


def test_cache_byte_quota_rejects_and_cleans_staging(settings, monkeypatch):
    monkeypatch.setenv("HF_CACHE_MAX_BYTES", "2")
    with client(settings) as api:
        result = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        assert result.status_code == 413
        assert result.json()["error"]["code"] == "CACHE_QUOTA_EXCEEDED"
        assert list((Path(settings.data_dir) / "snapshots").iterdir()) == []
        assert api.get("/api/v1/cache/items", headers=auth()).json()["data"] == []


def test_disk_watermark_stops_speculative_and_evicts_low_priority(settings, monkeypatch):
    with client(settings) as api:
        low_id = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(priority="low")).json()[
            "data"
        ]["id"]
        low_path = Path(settings.data_dir) / "snapshots" / low_id

        def disk_usage(_path):
            # Model the shared disk dropping below 85% after the low-priority
            # cache item is removed; no host disk state is changed.
            used = 88 if low_path.exists() else 80
            return SimpleNamespace(total=1_000_000, used=used * 10_000, free=(100 - used) * 10_000)

        monkeypatch.setattr(hf_cache.shutil, "disk_usage", disk_usage)
        assert (
            api.post(
                "/api/v1/cache/ensure",
                headers=auth(),
                json=ensure_payload(repo_id="org/spec", priority="low"),
            ).status_code
            == 507
        )
        ensured = api.post(
            "/api/v1/cache/ensure",
            headers=auth(),
            json=ensure_payload(repo_id="org/prod", priority="production"),
        )
        assert ensured.status_code == 200, ensured.text
        assert not low_path.exists()
        assert low_id not in {
            entry["id"] for entry in api.get("/api/v1/cache/items", headers=auth()).json()["data"]
        }


def wait_job(api, job_id):
    for _ in range(200):
        job = api.get(f"/api/v1/cache/prefetch/jobs/{job_id}", headers=auth()).json()["data"]
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.01)
    raise AssertionError(f"prefetch job did not finish: {job}")


def test_prefetch_is_async_persistent_and_reports_completion(settings):
    with client(settings) as api:
        queued = api.post("/api/v1/cache/prefetch", headers=auth(), json=ensure_payload(revision="main"))
        assert queued.status_code == 202
        job_id = queued.json()["data"]["id"]
        final = wait_job(api, job_id)
        assert final["status"] == "succeeded"
        assert final["progress"] == 100 and final["phase"] == "ready"
        assert final["attempts"] == 1 and final["item_id"]
        assert final["requested_revision"] == "main" and final["resolved_revision"] == REV
        assert api.get(f"/api/v1/cache/prefetch/jobs/{job_id}", headers=auth()).json()["data"] == final


def test_prefetch_retries_transient_backend_error(settings):
    calls = 0

    def flaky_download(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("transient fixture failure")
        return local_download(*args)

    api = TestClient(
        hf_cache.create_app(settings, download_backend=flaky_download, revision_resolver=lambda *_args: REV)
    )
    with api:
        job_id = api.post("/api/v1/cache/prefetch", headers=auth(), json=ensure_payload()).json()["data"][
            "id"
        ]
        job = wait_job(api, job_id)
        assert job["status"] == "succeeded" and job["attempts"] == 2


def test_dependency_candidates_require_human_accept_and_issue_signal_does_not_autodownload(settings):
    with client(settings) as api:
        signal = {
            "source": "issue",
            "repository": "platform",
            "commit": "d" * 40,
            "repo_id": "org/repo",
            "revision": "main",
            "confidence": 0.99,
            "reason": "mentioned in issue text",
        }
        created = api.post("/api/v1/signals/dependency", headers=auth("opencode"), json=signal)
        assert created.status_code == 200
        candidate_id = created.json()["data"]["candidate_id"]
        listing = api.get("/api/v1/prefetch/candidates", headers=auth()).json()["data"]
        candidate = next(row for row in listing if row["id"] == candidate_id)
        assert candidate["status"] == "pending" and candidate["job_id"] is None
        assert (
            api.post(
                f"/api/v1/prefetch/candidates/{candidate_id}/accept", headers=auth("opencode")
            ).status_code
            == 403
        )
        accepted = api.post(
            f"/api/v1/prefetch/candidates/{candidate_id}/accept", headers=auth("admin")
        ).json()["data"]
        assert accepted["status"] == "accepted"
        assert wait_job(api, accepted["job_id"])["status"] == "succeeded"


def test_candidate_reject_does_not_enqueue_job(settings):
    with client(settings) as api:
        signal = {
            "source": "requirements",
            "repository": "platform",
            "commit": "c" * 40,
            "repo_id": "org/repo",
            "revision": "v1",
            "confidence": 0.4,
            "reason": "optional dependency",
        }
        candidate_id = api.post("/api/v1/signals/dependency", headers=auth(), json=signal).json()["data"][
            "candidate_id"
        ]
        rejected = api.post(f"/api/v1/prefetch/candidates/{candidate_id}/reject", headers=auth("admin"))
        assert rejected.status_code == 200 and rejected.json()["data"]["status"] == "rejected"
        candidate = next(
            row
            for row in api.get("/api/v1/prefetch/candidates", headers=auth()).json()["data"]
            if row["id"] == candidate_id
        )
        assert candidate["job_id"] is None


def test_failed_prefetch_can_be_manually_retried(settings):
    def unavailable(*_args):
        raise APIError(404, "REPO_NOT_FOUND", "fixture repository unavailable")

    api = TestClient(
        hf_cache.create_app(settings, download_backend=unavailable, revision_resolver=lambda *_args: REV)
    )
    with api:
        job_id = api.post("/api/v1/cache/prefetch", headers=auth(), json=ensure_payload()).json()["data"][
            "id"
        ]
        first = wait_job(api, job_id)
        assert first["status"] == "failed" and first["attempts"] == 1
        queued = api.post(f"/api/v1/cache/prefetch/jobs/{job_id}/retry", headers=auth())
        assert queued.status_code == 200
        retried = wait_job(api, job_id)
        assert retried["status"] == "failed" and retried["attempts"] == 1


def test_startup_resumes_durable_queued_prefetch(settings):
    app = hf_cache.create_app(
        settings,
        download_backend=local_download,
        revision_resolver=lambda *_args: REV,
    )
    job_id = "durable-test-job"
    now = time.time()
    with transaction(Path(settings.data_dir) / "hf-cache.sqlite") as db:
        db.execute(
            "INSERT INTO prefetch_jobs(id,payload,status,progress,phase,attempts,max_attempts,item_id,error,created,updated) "
            "VALUES(?,?,'queued',0,'queued',0,3,NULL,'',?,?)",
            (job_id, hf_cache.Ensure(**ensure_payload(revision="main")).model_dump_json(), now, now),
        )
    with TestClient(app) as api:
        recovered = wait_job(api, job_id)
        assert recovered["status"] == "succeeded" and recovered["item_id"]


def test_official_hub_revision_resolver_uses_repo_info_sha(monkeypatch):
    import huggingface_hub

    calls = []

    class FakeHfApi:
        def repo_info(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(sha=REV)

    monkeypatch.setattr(huggingface_hub, "HfApi", FakeHfApi)
    assert hf_cache._resolve_hf_revision("dataset", "org/repo", "release/tag") == REV
    assert calls == [{"repo_id": "org/repo", "repo_type": "dataset", "revision": "release/tag"}]


def test_official_hub_downloader_checks_dry_run_quota_before_transfer(monkeypatch, tmp_path):
    import huggingface_hub

    calls = []

    def fake_snapshot_download(**kwargs):
        calls.append(kwargs)
        if kwargs.get("dry_run", False):
            return [SimpleNamespace(file_size=4)]
        destination = Path(kwargs["local_dir"])
        destination.mkdir(parents=True)
        (destination / "model.bin").write_bytes(b"data")
        return str(destination)

    monkeypatch.setattr(huggingface_hub, "snapshot_download", fake_snapshot_download)
    staging = tmp_path / "snapshot"
    with pytest.raises(APIError, match="CACHE_QUOTA_EXCEEDED"):
        hf_cache._hub_download("model", "org/repo", REV, staging, [], [], 3)
    assert len(calls) == 1 and calls[0]["dry_run"] is True
    assert not staging.exists()

    hf_cache._hub_download("model", "org/repo", REV, staging, [], [], 4)
    assert len(calls) == 3 and calls[-1].get("dry_run", False) is False
    assert (staging / "model.bin").read_bytes() == b"data"


def test_unavailable_physical_storage_provider_fails_closed(settings):
    def unavailable(_path):
        raise OSError("host telemetry unavailable")

    api = TestClient(
        hf_cache.create_app(
            settings,
            download_backend=local_download,
            revision_resolver=lambda *_args: REV,
            storage_usage_provider=unavailable,
        )
    )
    with api:
        status = api.get("/api/v1/cache/storage", headers=auth())
        assert status.status_code == 503
        assert status.json()["error"]["code"] == "STORAGE_TELEMETRY_UNAVAILABLE"
        result = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        assert result.status_code == 503
        assert list((Path(settings.data_dir) / "snapshots").iterdir()) == []


def write_pool_metrics(path, *, pool="aux-10t", total=10_000, used=1_000, observed_at=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pool": pool,
                "total": total,
                "used": used,
                "free": total - used,
                "observed_at": (observed_at or datetime.now(timezone.utc)).isoformat(),
            }
        )
    )
    path.chmod(0o600)
    return path


def test_root_fed_pool_metrics_schema_owner_and_freshness(tmp_path):
    path = write_pool_metrics(tmp_path / "pool.json")
    owners = []
    result = hf_cache.read_pool_metrics(
        path,
        expected_pool="aux-10t",
        owner_validator=lambda info: owners.append(info.st_uid) or info.st_uid == os.getuid(),
    )
    assert result == {"pool": "aux-10t", "total": 10_000, "used": 1_000, "free": 9_000}
    assert owners == [os.getuid()]
    with pytest.raises(APIError, match="STORAGE_TELEMETRY_UNAVAILABLE"):
        hf_cache.read_pool_metrics(path, owner_validator=lambda _info: False)


@pytest.mark.parametrize("fault", ["stale", "wrong_pool", "inconsistent", "tampered", "writable", "symlink"])
def test_root_fed_pool_metrics_rejects_untrusted_or_invalid_file(tmp_path, fault):
    real_path = write_pool_metrics(tmp_path / "pool.json")
    path = real_path
    if fault == "stale":
        write_pool_metrics(real_path, observed_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    elif fault == "wrong_pool":
        write_pool_metrics(real_path, pool="other-pool")
    elif fault == "inconsistent":
        real_path.write_text(
            json.dumps(
                {
                    "pool": "aux-10t",
                    "total": 10_000,
                    "used": 1_000,
                    "free": 8_000,
                    "observed_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        )
    elif fault == "tampered":
        real_path.write_text('{"pool":"aux-10t","total":')
    elif fault == "writable":
        real_path.chmod(0o666)
    elif fault == "symlink":
        path = tmp_path / "link.json"
        path.symlink_to(real_path)
    with pytest.raises(APIError, match="STORAGE_TELEMETRY_UNAVAILABLE"):
        hf_cache.read_pool_metrics(path, owner_validator=lambda _info: True)


def test_root_pool_watermarks_and_guest_free_space_are_both_enforced(settings, monkeypatch, tmp_path):
    metrics = write_pool_metrics(tmp_path / "pool.json", total=10_000, used=8_000)
    monkeypatch.setenv("HF_CACHE_POOL_METRICS_FILE", str(metrics))
    monkeypatch.setenv("HF_CACHE_EXPECTED_POOL", "aux-10t")

    def constrained_guest(_path):
        return SimpleNamespace(total=1_000, used=500, free=5)

    monkeypatch.setattr(hf_cache.shutil, "disk_usage", constrained_guest)
    api = TestClient(
        hf_cache.create_app(
            settings,
            download_backend=local_download,
            revision_resolver=lambda *_args: REV,
            pool_metrics_owner_validator=lambda _info: True,
        )
    )
    with api:
        status = api.get("/api/v1/cache/storage", headers=auth()).json()["data"]
        assert status["watermark"] == "stop_prefetch"
        assert status["pool_free_bytes"] == 2_000 and status["guest_free_bytes"] == 5
        assert status["free_bytes"] == 5 and status["used_percent"] == 80
        rejected = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(priority="low"))
        assert rejected.status_code == 507
        rejected = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        assert rejected.status_code == 413
        assert rejected.json()["error"]["code"] == "CACHE_QUOTA_EXCEEDED"


def test_root_pool_85_90_watermarks_evict_low_and_stop_new_downloads(settings, monkeypatch, tmp_path):
    metrics = write_pool_metrics(tmp_path / "pool.json", total=10_000, used=7_000)
    monkeypatch.setenv("HF_CACHE_POOL_METRICS_FILE", str(metrics))
    api = TestClient(
        hf_cache.create_app(
            settings,
            download_backend=local_download,
            revision_resolver=lambda *_args: REV,
            pool_metrics_owner_validator=lambda _info: True,
        )
    )
    with api:
        low_id = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/low", priority="low")
        ).json()["data"]["id"]
        write_pool_metrics(metrics, total=10_000, used=8_500)
        status85 = api.get("/api/v1/cache/storage", headers=auth()).json()["data"]
        assert status85["watermark"] == "evict_low_priority"
        ensured = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/normal"))
        assert ensured.status_code == 200
        assert api.get(f"/api/v1/cache/items/{low_id}", headers=auth()).status_code == 404
        write_pool_metrics(metrics, total=10_000, used=9_000)
        status90 = api.get("/api/v1/cache/storage", headers=auth()).json()["data"]
        assert status90["watermark"] == "retain_protected"
        blocked = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/blocked"))
        assert blocked.status_code == 507


def test_stale_pool_metrics_preserve_cached_reads_but_block_downloads(settings, monkeypatch, tmp_path):
    metrics = write_pool_metrics(tmp_path / "pool.json")
    monkeypatch.setenv("HF_CACHE_POOL_METRICS_FILE", str(metrics))
    api = TestClient(
        hf_cache.create_app(
            settings,
            download_backend=local_download,
            revision_resolver=lambda *_args: REV,
            pool_metrics_owner_validator=lambda _info: True,
        )
    )
    with api:
        item_id = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]["id"]
        write_pool_metrics(metrics, observed_at=datetime.now(timezone.utc) - timedelta(minutes=10))
        assert api.get("/api/v1/cache/items", headers=auth()).status_code == 200
        assert api.get(f"/api/v1/cache/items/{item_id}", headers=auth()).status_code == 200
        assert api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).status_code == 200
        gc = api.post("/api/v1/cache/gc", headers=auth(), json={"target_usage_percent": 75})
        assert gc.status_code == 503
        assert (settings.data_dir / "snapshots" / item_id).is_dir()
        new_item = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/new"))
        assert new_item.status_code == 503
        assert new_item.json()["error"]["code"] == "STORAGE_TELEMETRY_UNAVAILABLE"


def test_production_requires_pool_telemetry_for_ensure_and_prefetch(settings, monkeypatch):
    monkeypatch.delenv("HF_CACHE_POOL_METRICS_FILE", raising=False)
    production_settings = type(settings)(settings.data_dir, mode="production")
    downloads = []
    api = TestClient(
        hf_cache.create_app(
            production_settings,
            download_backend=lambda *args: downloads.append(args),
            revision_resolver=lambda *_args: REV,
        )
    )
    api.app.state.authorization_resolver = lambda _token: Identity(
        "operator", {"hf-cache:read", "hf-cache:write"}
    )
    with api:
        ready = api.get("/health/ready")
        storage = api.get("/api/v1/cache/storage", headers=auth())
        ensure = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        prefetch = api.post("/api/v1/cache/prefetch", headers=auth(), json=ensure_payload())
        assert ready.status_code == storage.status_code == ensure.status_code == prefetch.status_code == 503
        assert ready.json()["error"]["code"] == storage.json()["error"]["code"] == ensure.json()[
            "error"
        ]["code"] == prefetch.json()[
            "error"
        ]["code"] == (
            "STORAGE_TELEMETRY_UNAVAILABLE"
        )
        assert downloads == []
        with hf_cache.connect(settings.data_dir / "hf-cache.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM prefetch_jobs").fetchone()[0] == 0


def test_cache_domain_metrics_and_events_track_hits_downloads_and_evictions(settings, caplog):
    caplog.set_level(logging.INFO, "xeonolab")
    with client(settings) as api:
        first = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        hit = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        assert hit["status"] == "cached"
        assert api.post(f"/api/v1/cache/items/{first['id']}/evict", headers=auth()).status_code == 200
        metrics_response = api.get("/metrics")
        assert metrics_response.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"
        metrics = metrics_response.text
        assert "hfcache_usage_bytes 0\n" in metrics
        assert "hfcache_download_bytes_total " + str(first["bytes"]) + "\n" in metrics
        assert "hfcache_hit_total 1\n" in metrics
        assert "hfcache_miss_total 1\n" in metrics
        assert "hfcache_evicted_bytes_total " + str(first["bytes"]) + "\n" in metrics
    assert '"event": "cache.miss"' in caplog.text
    assert '"event": "cache.download.completed"' in caplog.text
    assert '"event": "cache.hit"' in caplog.text
    assert '"event": "cache.item.evicted"' in caplog.text


def test_gc_stops_after_one_item_when_pool_telemetry_does_not_advance(settings, monkeypatch, caplog):
    caplog.set_level(logging.INFO, "xeonolab")
    pool = {"used": 5_000}

    def static_pool(_path):
        return SimpleNamespace(
            pool="aux-10t", total=10_000, used=pool["used"], free=10_000 - pool["used"]
        )

    guest_samples = []

    def changing_guest(path):
        used = 4_000 + 100 * len(list(path.iterdir()))
        guest_samples.append(used)
        return SimpleNamespace(total=10_000, used=used, free=10_000 - used)

    monkeypatch.setattr(hf_cache.shutil, "disk_usage", changing_guest)
    api = TestClient(
        hf_cache.create_app(
            settings,
            download_backend=local_download,
            revision_resolver=lambda *_args: REV,
            storage_usage_provider=static_pool,
        )
    )
    with api:
        ids = [
            api.post(
                "/api/v1/cache/ensure",
                headers=auth(),
                json=ensure_payload(repo_id=f"org/gc-{index}"),
            ).json()["data"]["id"]
            for index in range(3)
        ]
        pool["used"] = 9_500
        guest_samples.clear()
        result = api.post(
            "/api/v1/cache/gc", headers=auth(), json={"target_usage_percent": 75}
        ).json()["data"]
        assert len(result["evicted"]) == 1
        assert guest_samples[0] != guest_samples[-1]
        assert result["evicted"][0] in ids
        assert result["target_reached"] is False
        remaining = api.get("/api/v1/cache/items", headers=auth()).json()["data"]
        assert len(remaining) == 2
    assert '"event": "cache.gc.telemetry_stalled"' in caplog.text



def test_production_cached_ensure_works_without_pool_metrics_or_hub(settings, monkeypatch):
    monkeypatch.delenv("HF_CACHE_POOL_METRICS_FILE", raising=False)
    with client(settings) as api:
        item = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]

    def unexpected(*_args):
        raise AssertionError("cached immutable artifact must not require Hub access or a download")

    app = hf_cache.create_app(
        type(settings)(settings.data_dir, mode="production"),
        download_backend=unexpected,
        revision_resolver=unexpected,
    )
    app.state.authorization_resolver = lambda _token: Identity(
        "operator", {"hf-cache:read", "hf-cache:write"}
    )
    with TestClient(app) as api:
        cached = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        assert cached.status_code == 200
        assert cached.json()["data"]["status"] == "cached"
        assert cached.json()["data"]["id"] == item["id"]
        assert api.get("/health/ready").status_code == 503
        assert api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/missing")
        ).status_code == 503
        assert api.post(
            "/api/v1/cache/prefetch", headers=auth(), json=ensure_payload()
        ).status_code == 503


@pytest.mark.parametrize("failed_read", [1, 2, 3])
def test_gc_telemetry_failure_keeps_database_and_files_consistent(settings, failed_read):
    state = {"gc": False, "reads": 0}

    def pool_usage(_path):
        if state["gc"]:
            state["reads"] += 1
            if state["reads"] == failed_read:
                raise OSError("fixture telemetry failed")
        used = 9_500 if state["gc"] else 5_000
        return SimpleNamespace(pool="aux-10t", total=10_000, used=used, free=10_000 - used)

    app = hf_cache.create_app(
        settings, download_backend=local_download,
        revision_resolver=lambda *_args: REV, storage_usage_provider=pool_usage,
    )
    with TestClient(app) as api:
        initial = [
            api.post(
                "/api/v1/cache/ensure", headers=auth(),
                json=ensure_payload(repo_id=f"org/failure-{index}"),
            ).json()["data"]
            for index in range(3)
        ]
        state["gc"] = True
        result = api.post("/api/v1/cache/gc", headers=auth(), json={"target_usage_percent": 75})
        assert result.status_code == 503
        assert result.json()["error"]["code"] == "STORAGE_TELEMETRY_UNAVAILABLE"
        remaining = api.get("/api/v1/cache/items", headers=auth()).json()["data"]
        remaining_ids = {item["id"] for item in remaining}
        deleted = [item for item in initial if item["id"] not in remaining_ids]
        assert len(deleted) == (1 if failed_read == 3 else 0)
        for item in initial:
            folder = settings.data_dir / "snapshots" / item["id"]
            assert folder.exists() == (item["id"] in remaining_ids)
        for item in remaining:
            cached = api.post(
                "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id=item["repo_id"])
            )
            assert cached.status_code == 200 and cached.json()["data"]["status"] == "cached"
        evicted_bytes = sum(item["bytes"] for item in deleted)
        assert f"hfcache_evicted_bytes_total {evicted_bytes}\n" in api.get("/metrics").text


def test_cache_metrics_database_failure_is_bounded_and_truthful(settings, monkeypatch, caplog):
    caplog.set_level(logging.WARNING, "xeonolab")
    with client(settings) as api:
        first = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        assert first.status_code == 200
        original_connect = hf_cache.connect

        def unavailable(_path):
            raise OSError("private repository secret must not be logged")

        monkeypatch.setattr(hf_cache, "connect", unavailable)
        result = api.get("/metrics")
        assert result.status_code == 200
        assert 'xeonolab_metrics_extra_available{service="hf-cache"} 0\n' in result.text
        assert "xeonolab_build_info" in result.text
        assert "xeonolab_route_requests_total" in result.text
        assert "hfcache_usage_bytes" not in result.text
        assert "hfcache_download_bytes_total" not in result.text
        assert "private repository secret" not in caplog.text
        monkeypatch.setattr(hf_cache, "connect", original_connect)
        recovered = api.get("/metrics")
        assert 'xeonolab_metrics_extra_available{service="hf-cache"} 1\n' in recovered.text
        assert f"hfcache_usage_bytes {first.json()['data']['bytes']}\n" in recovered.text
    assert '"event": "metrics.extra_failed"' in caplog.text


def test_private_repository_id_is_absent_from_cache_logs(settings, caplog):
    caplog.set_level(logging.INFO, "xeonolab")
    with client(settings) as api:
        result = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="private-org/private-model")
        )
        assert result.status_code == 200
        assert '"event": "cache.miss"' in caplog.text
        assert result.json()["data"]["id"] in caplog.text
    assert "private-org/private-model" not in caplog.text
    assert "repo_id" not in caplog.text


def test_watermark_transitions_emit_plan_storage_warning_once(settings, caplog):
    caplog.set_level(logging.INFO, "xeonolab")
    state = {"used": 7_000}
    app = hf_cache.create_app(
        settings,
        storage_usage_provider=lambda _path: SimpleNamespace(
            pool="aux-10t", total=10_000, used=state["used"], free=10_000 - state["used"]
        ),
    )
    with TestClient(app) as api:
        for used in (7_000, 8_000, 8_000, 8_500, 9_000, 7_000):
            state["used"] = used
            assert api.get("/api/v1/cache/storage", headers=auth()).status_code == 200
    events = [
        json.loads(record.message) for record in caplog.records
        if '"event": "hfcache.storage.warning"' in record.message
    ]
    assert [event["resource"]["watermark"] for event in events] == [
        "stop_prefetch", "evict_low_priority", "retain_protected", "normal"
    ]
    assert [event["resource"]["previous_watermark"] for event in events] == [
        "normal", "stop_prefetch", "evict_low_priority", "retain_protected"
    ]
    assert len({event["event_id"] for event in events}) == 4
    for event in events:
        assert event["type"] == "hfcache.storage.warning"
        assert event["source"] == "hf-cache" and event["actor"] == "system"
        assert event["occurred_at"] and event["request_id"]



@pytest.mark.parametrize("operation", ["evict", "gc"])
def test_eviction_catalog_commit_failure_preserves_files_and_cache(settings, monkeypatch, operation):
    app = hf_cache.create_app(
        settings, download_backend=local_download, revision_resolver=lambda *_args: REV,
    )
    with TestClient(app, raise_server_exceptions=False) as api:
        item = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        original_transaction = hf_cache.transaction
        injected = []

        @contextmanager
        def fail_catalog_commit(path):
            with original_transaction(path) as db:
                yield db
                if not injected and db.execute("SELECT 1 FROM pending_evictions LIMIT 1").fetchone():
                    injected.append(True)
                    raise sqlite3.OperationalError("fixture catalog commit failure")

        monkeypatch.setattr(hf_cache, "transaction", fail_catalog_commit)
        if operation == "gc":
            monkeypatch.setattr(
                hf_cache.shutil, "disk_usage",
                lambda _path: SimpleNamespace(total=10_000, used=9_500, free=500),
            )
            result = api.post("/api/v1/cache/gc", headers=auth(), json={"target_usage_percent": 75})
        else:
            result = api.post(f"/api/v1/cache/items/{item['id']}/evict", headers=auth())
        assert result.status_code == 500 and injected == [True]
        with hf_cache.connect(settings.data_dir / "hf-cache.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM pending_evictions").fetchone()[0] == 0
        assert (settings.data_dir / "snapshots" / item["id"] / "weights.bin").is_file()
        cached = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload())
        assert cached.status_code == 200 and cached.json()["data"]["status"] == "cached"


def test_process_crash_after_eviction_journal_commit_recovers_on_restart(settings):
    with client(settings) as api:
        item = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
    crash_code = """
import os
import sys
from pathlib import Path
from fastapi.testclient import TestClient
from xeonolab import hf_cache
from xeonolab.common import Settings
settings = Settings(Path(sys.argv[1]), "development", dev_tokens={
    "operator": {"sub": "operator", "scope": "hf-cache:write"}
})
app = hf_cache.create_app(settings)
with TestClient(app) as api:
    hf_cache.shutil.rmtree = lambda *_args, **_kwargs: os._exit(77)
    api.post("/api/v1/cache/items/" + sys.argv[2] + "/evict",
             headers={"Authorization": "Bearer operator"})
"""
    crashed = subprocess.run(
        [sys.executable, "-c", crash_code, str(settings.data_dir), item["id"]],
        capture_output=True, timeout=15,
    )
    assert crashed.returncode == 77, crashed.stderr.decode()
    folder = settings.data_dir / "snapshots" / item["id"]
    assert folder.is_dir()
    with hf_cache.connect(settings.data_dir / "hf-cache.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert db.execute("SELECT bytes FROM pending_evictions").fetchone()[0] == item["bytes"]
    with client(settings) as api:
        assert not folder.exists()
        assert api.get("/api/v1/cache/items", headers=auth()).json()["data"] == []
        metrics = api.get("/metrics").text
        assert "hfcache_usage_bytes 0\n" in metrics
        assert "hfcache_pending_eviction_bytes 0\n" in metrics
        assert f"hfcache_evicted_bytes_total {item['bytes']}\n" in metrics
    with hf_cache.connect(settings.data_dir / "hf-cache.sqlite") as db:
        assert db.execute("SELECT COUNT(*) FROM pending_evictions").fetchone()[0] == 0
    with client(settings) as api:
        # A second restart has nothing to clean and does not recount the eviction.
        assert "hfcache_evicted_bytes_total 0\n" in api.get("/metrics").text


def test_partial_eviction_cleanup_failure_reserves_bytes_blocks_downloads_and_retries(settings, monkeypatch):
    downloads = []

    def counted_download(*args):
        downloads.append(args)
        local_download(*args)

    def make_app():
        return hf_cache.create_app(
            settings, download_backend=counted_download, revision_resolver=lambda *_args: REV,
        )

    with TestClient(make_app()) as api:
        doomed = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        kept = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/kept", pin=True)
        ).json()["data"]
        original_rmtree = hf_cache.shutil.rmtree

        def partial_failure(path, *args, **kwargs):
            if path != settings.data_dir / "snapshots" / doomed["id"]:
                return original_rmtree(path, *args, **kwargs)
            weights = path / "weights.bin"
            if weights.exists():
                weights.unlink()
            raise OSError("fixture disk failure")

        monkeypatch.setattr(hf_cache.shutil, "rmtree", partial_failure)
        failed = api.post(f"/api/v1/cache/items/{doomed['id']}/evict", headers=auth())
        assert failed.status_code == 503
        assert failed.json()["error"]["code"] == "CACHE_EVICTION_PENDING"
        assert api.get(f"/api/v1/cache/items/{doomed['id']}", headers=auth()).status_code == 404
        assert (settings.data_dir / "snapshots" / doomed["id"]).is_dir()
        reserved = doomed["bytes"] + kept["bytes"]
        assert api.get("/api/v1/cache/storage", headers=auth()).json()["data"]["cached_bytes"] == reserved
        assert f"hfcache_usage_bytes {reserved}\n" in api.get("/metrics").text
        assert f"hfcache_pending_eviction_bytes {doomed['bytes']}\n" in api.get("/metrics").text
    # A failed startup cleanup keeps verified cache reads usable, but blocks allocation.
    with TestClient(make_app()) as api:
        assert api.get("/health/ready").status_code == 503
        cached = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/kept")
        )
        assert cached.status_code == 200 and cached.json()["data"]["status"] == "cached"
        blocked = api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/new")
        )
        assert blocked.status_code == 503 and blocked.json()["error"]["code"] == "CACHE_EVICTION_PENDING"
        assert len(downloads) == 2
        monkeypatch.setattr(hf_cache.shutil, "rmtree", original_rmtree)
        retried = api.post(f"/api/v1/cache/items/{doomed['id']}/evict", headers=auth())
        assert retried.status_code == 200
        assert not (settings.data_dir / "snapshots" / doomed["id"]).exists()
        assert api.get("/health/ready").status_code == 200
        assert api.get("/api/v1/cache/storage", headers=auth()).json()["data"]["cached_bytes"] == kept["bytes"]
        assert f"hfcache_evicted_bytes_total {doomed['bytes']}\n" in api.get("/metrics").text
        with hf_cache.connect(settings.data_dir / "hf-cache.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM pending_evictions").fetchone()[0] == 0
        assert api.post(
            "/api/v1/cache/ensure", headers=auth(), json=ensure_payload(repo_id="org/new")
        ).status_code == 200
        assert len(downloads) == 3


def test_eviction_journal_clear_commit_failure_recovers_after_files_are_gone(settings, monkeypatch):
    with client(settings) as api:
        item = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        original_transaction = hf_cache.transaction
        injected = []

        @contextmanager
        def fail_journal_clear_commit(path):
            with original_transaction(path) as db:
                had_pending = bool(db.execute("SELECT 1 FROM pending_evictions LIMIT 1").fetchone())
                yield db
                if had_pending and not injected and not db.execute(
                    "SELECT 1 FROM pending_evictions LIMIT 1"
                ).fetchone():
                    injected.append(True)
                    raise sqlite3.OperationalError("fixture journal clearance commit failure")

        monkeypatch.setattr(hf_cache, "transaction", fail_journal_clear_commit)
        failed = api.post(f"/api/v1/cache/items/{item['id']}/evict", headers=auth())
        assert failed.status_code == 503 and injected == [True]
        assert not (settings.data_dir / "snapshots" / item["id"]).exists()
        with hf_cache.connect(settings.data_dir / "hf-cache.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
            assert db.execute("SELECT bytes FROM pending_evictions").fetchone()[0] == item["bytes"]
        # Conservatively keep the reservation until the durable journal is cleared.
        assert f"hfcache_usage_bytes {item['bytes']}\n" in api.get("/metrics").text
        assert "hfcache_evicted_bytes_total 0\n" in api.get("/metrics").text
        retried = api.post(f"/api/v1/cache/items/{item['id']}/evict", headers=auth())
        assert retried.status_code == 200
        assert "hfcache_usage_bytes 0\n" in api.get("/metrics").text
        assert f"hfcache_evicted_bytes_total {item['bytes']}\n" in api.get("/metrics").text


def test_eviction_recovery_refuses_snapshot_symlink(settings, monkeypatch, tmp_path):
    with client(settings) as api:
        item = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        original_rmtree = hf_cache.shutil.rmtree
        def unavailable(*_args):
            raise OSError("fixture deletion unavailable")

        monkeypatch.setattr(hf_cache.shutil, "rmtree", unavailable)
        assert api.post(f"/api/v1/cache/items/{item['id']}/evict", headers=auth()).status_code == 503
        monkeypatch.setattr(hf_cache.shutil, "rmtree", original_rmtree)
    folder = settings.data_dir / "snapshots" / item["id"]
    original_rmtree(folder)
    outside = tmp_path / "unrelated-data"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    folder.symlink_to(outside, target_is_directory=True)
    with client(settings) as api:
        assert (outside / "keep").read_text() == "keep"
        assert api.get("/health/ready").status_code == 503
        assert api.post(f"/api/v1/cache/items/{item['id']}/evict", headers=auth()).status_code == 500
        with hf_cache.connect(settings.data_dir / "hf-cache.sqlite") as db:
            assert db.execute("SELECT COUNT(*) FROM pending_evictions").fetchone()[0] == 1
