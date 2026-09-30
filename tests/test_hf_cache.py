from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from xeonolab import hf_cache

from .conftest import auth

REV = "a" * 40


def local_download(repo_type, repo_id, revision, destination, allow_patterns, ignore_patterns, max_bytes):
    destination.mkdir(parents=True)
    (destination / "config.json").write_text('{"model":"fixture"}')
    (destination / "weights.bin").write_bytes(b"local-test-weights")


def client(settings):
    return TestClient(hf_cache.create_app(settings, download_backend=local_download))


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
        assert (
            api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload(revision="main")).status_code
            == 422
        )
        result = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        assert result["status"] == "ready"
        assert result["revision"] == REV
        assert len(result["files"]) == 2
        item_id = result["id"]
        folder = Path(settings.data_dir) / "snapshots" / item_id
        assert (folder / ".xeonolab-manifest.json").is_file()
        again = api.post("/api/v1/cache/ensure", headers=auth(), json=ensure_payload()).json()["data"]
        assert again["status"] == "cached" and again["id"] == item_id
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
            return SimpleNamespace(total=100, used=used, free=100 - used)

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
