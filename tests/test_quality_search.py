"""Pipeline acceptance with explicitly simulated model vectors; no Qwen quality claims."""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from tokenizers import Tokenizer, models, pre_tokenizers

from xeonolab.common import APIError
from xeonolab.quality_cache import QualityCache
from xeonolab.services import create_app

from .conftest import auth, put_acl
from .test_qdrant_search import configure_qdrant


@pytest.fixture
def quality_library(settings, tmp_path, monkeypatch):
    path = tmp_path / "quality-test-tokenizer.json"
    words = [
        "[UNK]",
        "[PATH]",
        "[TABLE]",
        "[COLUMN]",
        "[TEXT]",
        "/quality/test",
        "table",
        "body",
        "alpha",
        "beta",
    ]
    tokenizer = Tokenizer(
        models.WordLevel(vocab={word: i for i, word in enumerate(words)}, unk_token="[UNK]")
    )
    tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
    tokenizer.save(str(path))
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "synthetic-test-token")
    qdrant = configure_qdrant(tmp_path, monkeypatch)
    client = TestClient(create_app("xelibrary", settings))
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/quality/test",
            "name": "table",
            "columns": [
                {"name": "body", "type": "string", "nullable": False},
                {"name": "group", "type": "int64", "nullable": False},
            ],
        },
    )
    assert created.status_code == 201
    tid = created.json()["data"]["table_id"]
    rows = {}
    for text, group in [("alpha", 1), ("beta", 2)]:
        created = client.post(
            f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"body": text, "group": group}}
        )
        assert created.status_code == 201
        rows[text] = created.json()["data"]["_row_id"]
    indexed = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    assert (
        client.get("/api/v1/jobs/" + indexed.json()["data"]["job_id"], headers=auth("admin")).json()["data"][
            "status"
        ]
        == "completed"
    )
    for point in qdrant["points"]:
        point["candidate_score"] = 0.95 if point["payload"]["row_id"] == rows["alpha"] else 0.7
    calls = []
    control = {
        "revision": "synthetic-qwen-v1",
        "preprocessing": "text-v1",
        "dimensions": 2,
        "on_query": None,
        "on_document": None,
        "error": None,
    }

    class SimulatedQualityClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def descriptor(self):
            if control["error"]:
                raise control["error"]
            return SimpleNamespace(
                model_id="Qwen/Qwen3-VL-Embedding-2B",
                model_revision=control["revision"],
                dimensions=control["dimensions"],
                preprocessing_revision=control["preprocessing"],
                max_batch_size=1,
                max_tokens=128,
                normalized=True,
                space_key=(
                    "Qwen/Qwen3-VL-Embedding-2B",
                    control["revision"],
                    control["dimensions"],
                    control["preprocessing"],
                ),
            )

        def encode(self, texts, descriptor, purpose="document"):
            calls.append((purpose, list(texts)))
            hook = control["on_" + purpose]
            if hook:
                control["on_" + purpose] = None
                hook()
            return [
                ([1.0, 0.0] if purpose == "query" or "beta" in text else [0.0, 1.0])
                + [0.0] * (descriptor.dimensions - 2)
                for text in texts
            ]

    client.app.state.quality_pipeline.client_factory = SimulatedQualityClient
    return client, tid, rows, qdrant, calls, control


def find(client, mode="quality", token="alice", endpoint="semantic", **body):
    return client.post(
        "/api/v1/search/" + endpoint, headers=auth(token), json={"query": "alpha", "mode": mode, **body}
    )


def test_quality_reranks_real_authorized_shortlist_and_cache(quality_library):
    client, tid, rows, qdrant, calls, _ = quality_library
    fast = find(client, mode="fast")
    assert [row["row_id"] for row in fast.json()["data"]] == [rows["alpha"], rows["beta"]]
    quality = find(client, limit=1)
    assert quality.status_code == 200, quality.text
    assert quality.json()["data"][0]["row_id"] == rows["beta"]
    assert quality.json()["data"][0]["score_type"] == "quality_rrf"
    assert quality.json()["data"][0]["snippet"] == "beta"
    assert "_chunks" not in quality.json()["data"][0]
    assert quality.json()["meta"]["encoded_chunks"] == 2
    assert quality.json()["meta"]["quality_dimensions"] == 2  # deliberately simulated, not production 2048D
    assert qdrant["query_limits"][-1] == 100
    again = find(client)
    assert again.json()["meta"]["cache_hits"] == 2
    assert again.json()["meta"]["encoded_chunks"] == 0
    assert len([call for call in calls if call[0] == "document"]) == 2
    assert len([call for call in calls if call[0] == "query"]) == 2
    assert find(client, limit=100).status_code == 200
    assert qdrant["query_limits"][-1] == 500
    metrics = client.get("/metrics").text
    assert 'xelibrary_quality_requests_total{result="ok"} 3' in metrics
    assert 'xelibrary_quality_cache_total{result="hit"} 4' in metrics
    assert 'xelibrary_quality_stage_seconds_count{stage="query"}' in metrics


@pytest.mark.parametrize("change", ["revision", "preprocessing", "dimensions", "actor"])
def test_quality_cache_isolates_identity_and_model_space(quality_library, change):
    client, tid, _, _, _, control = quality_library
    assert find(client).json()["meta"]["encoded_chunks"] == 2
    token = "alice"
    if change == "actor":
        assert put_acl(client, "table", tid, {"alice": ["read"], "bob": ["read"]}).status_code == 200
        token = "bob"
    elif change == "dimensions":
        control[change] = 3
    else:
        control[change] = "synthetic-v2"
    updated = find(client, token=token)
    assert updated.status_code == 200
    assert updated.json()["meta"]["cache_hits"] == 0
    assert updated.json()["meta"]["encoded_chunks"] == 2


@pytest.mark.parametrize("change", ["row_acl", "table_acl", "delete", "patch", "move", "local_index"])
@pytest.mark.parametrize("cached", [False, True])
def test_quality_rechecks_live_acl_and_source_after_worker_wait(quality_library, settings, change, cached):
    client, tid, rows, _, _, control = quality_library
    if cached:
        assert find(client).status_code == 200
    rid = rows["beta"]

    def mutate():
        if change in ("row_acl", "table_acl"):
            assert (
                put_acl(
                    client,
                    "row" if change == "row_acl" else "table",
                    f"{tid}:{rid}" if change == "row_acl" else tid,
                    {},
                ).status_code
                == 200
            )
        elif change in ("patch", "delete"):
            current = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin"))
            if change == "patch":
                result = client.patch(
                    f"/api/v1/tables/{tid}/rows/{rid}",
                    headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
                    json={"values": {"body": "alpha"}},
                )
            else:
                result = client.delete(
                    f"/api/v1/tables/{tid}/rows/{rid}",
                    headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
                )
            assert result.status_code == 200
        elif change == "move":
            current = client.get(f"/api/v1/tables/{tid}", headers=auth("admin"))
            assert (
                client.post(
                    f"/api/v1/tables/{tid}/move",
                    headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
                    json={"path": "/moved/test"},
                ).status_code
                == 200
            )
        else:
            with sqlite3.connect(settings.data_dir / "tables" / f"{tid}.xl") as db:
                db.execute("DELETE FROM __xl_vectors WHERE row_id=?", (rid,))

    control["on_query"] = mutate
    response = find(client)
    assert response.status_code == 200, response.text
    assert rid not in [row["row_id"] for row in response.json()["data"]]
    assert response.json()["meta"]["partial"] is True
    if change in ("table_acl", "move"):
        assert response.json()["data"] == []


def test_quality_revalidates_after_candidate_encoding_before_cache_publish(quality_library):
    client, tid, rows, _, calls, control = quality_library
    control["on_document"] = lambda: put_acl(client, "table", tid, {})
    result = find(client)
    assert result.status_code == 200
    assert result.json()["data"] == []
    assert client.app.state.quality_pipeline.cache.count() == 0
    assert len([call for call in calls if call[0] == "document"]) == 1
    assert result.json()["meta"]["encoded_chunks"] == 1


def test_quality_scope_path_field_filters_and_chunk_deduplication(quality_library, settings):
    client, _, rows, qdrant, calls, _ = quality_library
    settings.dev_tokens["reader"] = {"sub": "alice", "scope": "xelibrary:read", "agent_kind": "human"}
    assert find(client, token="reader").status_code == 403
    filtered = find(client, filters={"group": 2})
    assert [row["row_id"] for row in filtered.json()["data"]] == [rows["beta"]]
    assert len([call for call in calls if call[0] == "document"]) == 1
    qdrant["points"] *= 3
    duplicated = find(client)
    assert len(duplicated.json()["data"]) == 2
    assert duplicated.json()["meta"]["encoded_chunks"] == 1
    wrong_path = find(client, path="/other/**")
    assert wrong_path.json()["data"] == []
    assert wrong_path.json()["meta"]["encoded_chunks"] == 0


@pytest.mark.parametrize(
    "code,status",
    [
        ("QWEN_UNAVAILABLE", 503),
        ("QWEN_PROTOCOL_ERROR", 503),
        ("QUALITY_BUSY", 429),
        ("QUALITY_INPUT_REJECTED", 422),
    ],
)
def test_quality_provider_errors_do_not_fall_back_to_fast(quality_library, code, status):
    client, _, _, _, _, control = quality_library
    control["error"] = APIError(status, code, "synthetic provider failure")
    result = find(client)
    assert result.status_code == status
    assert result.json()["error"]["code"] == code
    assert "data" not in result.json()
    assert not client.app.state.quality_pipeline.lock.locked()
    assert find(client, mode="fast").status_code == 200
    metrics = client.get("/metrics").text
    assert f'xelibrary_quality_errors_total{{code="{code}"}} 1' in metrics


def test_quality_concurrency_gate_preserves_fast_and_releases(quality_library):
    client, _, _, _, _, _ = quality_library
    pipeline = client.app.state.quality_pipeline
    pipeline.lock.acquire()
    try:
        busy = find(client)
        assert busy.status_code == 429
        assert busy.headers["Retry-After"] == "1"
        assert find(client, mode="fast").status_code == 200
    finally:
        pipeline.lock.release()
    assert find(client).status_code == 200


def test_hybrid_fuses_filtered_fts_and_quality_ranks_without_early_final_limit(quality_library):
    client, _, rows, _, _, _ = quality_library
    result = find(client, endpoint="hybrid", limit=1)
    assert result.status_code == 200, result.text
    # FTS supplies alpha; quality ranking supplies both despite final limit=1.
    assert result.json()["data"][0]["row_id"] == rows["alpha"]
    assert result.json()["data"][0]["score_type"] == "hybrid_rrf"
    filtered = find(client, endpoint="hybrid", filters={"group": 2})
    assert [hit["row_id"] for hit in filtered.json()["data"]] == [rows["beta"]]
    auto = find(client, endpoint="hybrid", mode="auto")
    assert auto.status_code == 200
    assert auto.json()["meta"]["actual_mode"] == "fast"
    spec = client.app.openapi()
    for endpoint in ("semantic", "hybrid", "text"):
        responses = spec["paths"]["/api/v1/search/" + endpoint]["post"]["responses"]
        assert responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith("SearchResponse")
        assert set(responses) >= {"403", "429", "503"}


def test_quality_cache_is_bounded_ttl_and_purge_invalidation(quality_library):
    clock = [0]
    cache = QualityCache(max_entries=1, max_bytes=4096, ttl=2, clock=lambda: clock[0])
    first = ("namespace", "actor", "space", "table", "row1")
    second = ("namespace", "actor", "space", "table", "row2")
    assert cache.put(first, [1.0, 0.0]) == 0
    assert cache.put(second, [0.0, 1.0]) == 1
    assert cache.get(first) is None
    assert cache.get(second) == [0.0, 1.0]
    clock[0] = 2
    assert cache.get(second) is None
    assert cache.count() == 0
    oversized = QualityCache(max_bytes=1)
    assert oversized.put(first, [1.0, 0.0]) == 0
    assert oversized.count() == 0
    client, tid, rows, _, _, _ = quality_library
    assert find(client).json()["meta"]["encoded_chunks"] == 2
    current = client.get(f"/api/v1/tables/{tid}/rows/{rows['beta']}", headers=auth("admin"))
    removed = client.delete(
        f"/api/v1/tables/{tid}/rows/{rows['beta']}?purge=true",
        headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
    )
    assert removed.status_code == 200
    assert client.app.state.quality_pipeline.cache.count() == 1


def test_quality_total_budget_stops_next_batch_and_releases_gate(quality_library):
    client, _, _, _, calls, control = quality_library
    clock = [0.0]
    pipeline = client.app.state.quality_pipeline
    pipeline.clock = lambda: clock[0]
    pipeline.time_budget = 2
    control["on_document"] = lambda: clock.__setitem__(0, 3.0)
    result = find(client)
    assert result.status_code == 503
    assert result.json()["error"]["code"] == "QUALITY_TIMEOUT"
    assert "data" not in result.json()
    assert len([call for call in calls if call[0] == "document"]) == 1
    assert not pipeline.lock.locked()
    assert pipeline.cache.count() == 0
    assert find(client, mode="fast").status_code == 200
    assert find(client).status_code == 200


@pytest.mark.parametrize("change", ["row", "path", "fast_model"])
def test_source_rebuild_invalidates_quality_cache_keys(quality_library, monkeypatch, change):
    from .test_qdrant_search import FakeEmbeddingResponse

    client, tid, rows, _, _, _ = quality_library
    assert find(client).json()["meta"]["encoded_chunks"] == 2
    if change == "row":
        current = client.get(f"/api/v1/tables/{tid}/rows/{rows['alpha']}", headers=auth("admin"))
        assert (
            client.patch(
                f"/api/v1/tables/{tid}/rows/{rows['alpha']}",
                headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
                json={"values": {"body": "beta"}},
            ).status_code
            == 200
        )
    elif change == "path":
        current = client.get(f"/api/v1/tables/{tid}", headers=auth("admin"))
        assert (
            client.post(
                f"/api/v1/tables/{tid}/move",
                headers=auth("admin", **{"If-Match": current.headers["ETag"]}),
                json={"path": "/quality/moved"},
            ).status_code
            == 200
        )
    else:
        monkeypatch.setattr(
            FakeEmbeddingResponse,
            "json",
            lambda self: {
                "data": [{"embedding": [0.1] * 256}],
                "model_revision": "synthetic-fast-v2",
            },
        )
    started = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    assert (
        client.get("/api/v1/jobs/" + started.json()["data"]["job_id"], headers=auth("admin")).json()["data"][
            "status"
        ]
        == "completed"
    )
    result = find(client)
    assert result.status_code == 200
    assert result.json()["meta"]["encoded_chunks"] == (1 if change == "row" else 2)
    assert result.json()["meta"]["cache_hits"] == (1 if change == "row" else 0)


def test_quality_unexpected_error_is_observable_and_releases_gate(quality_library):
    client, _, _, _, _, control = quality_library

    def fail():
        raise RuntimeError("synthetic internal failure")

    control["on_query"] = fail
    with TestClient(client.app, raise_server_exceptions=False) as observer:
        result = find(observer)
        assert result.status_code == 500
        assert result.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "synthetic internal failure" not in result.text
        metrics = observer.get("/metrics").text
    assert 'xelibrary_quality_errors_total{code="INTERNAL_ERROR"} 1' in metrics
    assert not client.app.state.quality_pipeline.lock.locked()
    assert find(client).status_code == 200


def test_search_422_contract_matches_validation_and_worker_input_rejection(quality_library):
    from xeonolab.search_models import SearchErrorResponse

    client, _, _, _, _, control = quality_library
    spec = client.app.openapi()
    for endpoint in ("semantic", "text", "hybrid"):
        path = "/api/v1/search/" + endpoint
        invalid = client.post(path, headers=auth(), json={"query": "", "mode": "quality"})
        assert invalid.status_code == 422
        validated = SearchErrorResponse.model_validate(invalid.json())
        assert validated.error.code == "VALIDATION_ERROR"
        assert validated.error.details == {}
        assert validated.error.request_id
        assert (
            spec["paths"][path]["post"]["responses"]["422"]["content"]["application/json"]["schema"]["$ref"]
            == "#/components/schemas/SearchErrorResponse"
        )
    control["error"] = APIError(422, "QUALITY_INPUT_REJECTED", "synthetic input rejection")
    rejected = find(client)
    assert rejected.status_code == 422
    validated = SearchErrorResponse.model_validate(rejected.json())
    assert validated.error.code == "QUALITY_INPUT_REJECTED"
    assert validated.error.details == {}
    assert validated.error.request_id
