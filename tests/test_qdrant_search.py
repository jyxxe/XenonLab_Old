import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

from xeonolab import search
from xeonolab.services import create_app

from .conftest import auth, put_acl


class FakeEmbeddingResponse:
    status_code = 200

    def json(self):
        return {"data": [{"embedding": [0.1] * 256}], "model_revision": "d100m-test-v1"}


class FakeEmbeddingClient:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def post(self, *args, **kwargs):
        return FakeEmbeddingResponse()


def qdrant_transport(*, fail_upsert=False, fail_delete=False, mutate_query=None):
    state = {"created": False, "points": [], "fail_upsert": fail_upsert, "fail_delete": fail_delete, "query_limits": []}

    def handle(request):
        assert request.headers.get("api-key") == "test-key"
        assert request.url.scheme == "https"
        path = request.url.path
        if request.method == "GET" and path.endswith("/collections/fast_text"):
            if not state["created"]:
                return httpx.Response(404, json={"status": {"error": "missing"}})
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "result": {
                        "config": {
                            "params": {
                                "vectors": {"fast_text": {"size": 256, "distance": "Cosine"}}
                            }
                        }
                    },
                },
            )
        if request.method == "PUT" and path.endswith("/collections/fast_text"):
            state["created"] = True
            return httpx.Response(200, json={"status": "ok", "result": True})
        if request.method == "PUT" and path.endswith("/points"):
            if state["fail_upsert"]:
                return httpx.Response(500, json={"status": {"error": "write failed"}})
            points = request.read()
            by_id = {point["id"]: point for point in state["points"]}
            by_id.update({point["id"]: point for point in json.loads(points)["points"]})
            state["points"] = list(by_id.values())
            return httpx.Response(200, json={"status": "ok", "result": {"status": "completed"}})
        if request.method == "POST" and path.endswith("/points/query"):
            query = json.loads(request.read())
            state["query_limits"].append(query["limit"])
            points = state["points"]
            if mutate_query:
                points = mutate_query(points)
            payload = {
                    "status": "ok",
                    "result": {
                        "points": [
                            {
                                "id": p.get("candidate_id", p["id"]),
                                "score": p.get("candidate_score", 0.9),
                                "payload": p["payload"],
                            }
                            for p in points[: query["limit"]]
                        ]
                    },
                }
            return httpx.Response(
                200,
                content=json.dumps(payload, allow_nan=True),
                headers={"content-type": "application/json"},
            )
        if request.method == "POST" and path.endswith("/points/delete"):
            if state["fail_delete"]:
                return httpx.Response(500, json={"status": {"error": "delete failed"}})
            body = json.loads(request.read())
            if "points" in body:
                ids = set(body["points"])
                state["points"] = [point for point in state["points"] if point["id"] not in ids]
            else:
                conditions = body["filter"]["must"]
                state["points"] = [
                    point
                    for point in state["points"]
                    if not all(point["payload"].get(item["key"]) == item["match"]["value"] for item in conditions)
                ]
            return httpx.Response(200, json={"status": "ok", "result": {"status": "completed"}})
        return httpx.Response(404, json={"status": {"error": "unexpected"}})

    return httpx.MockTransport(handle), state


def configure_qdrant(tmp_path, monkeypatch, *, fail_upsert=False, fail_delete=False, mutate_query=None):
    key_file = tmp_path / "qdrant.key"
    key_file.write_text("test-key\n")
    monkeypatch.setenv("QDRANT_URL", "https://qdrant.test")
    monkeypatch.setenv("QDRANT_API_KEY_FILE", str(key_file))
    monkeypatch.delenv("QDRANT_CA_FILE", raising=False)
    transport, state = qdrant_transport(
        fail_upsert=fail_upsert, fail_delete=fail_delete, mutate_query=mutate_query
    )
    real_client = httpx.Client
    real_adapter = search.QdrantAdapter

    def client_factory(*args, **kwargs):
        if kwargs.get("transport") is not None:
            return real_client(*args, **kwargs)
        return FakeEmbeddingClient(*args, **kwargs)

    monkeypatch.setattr(search.httpx, "Client", client_factory)
    monkeypatch.setattr(
        search,
        "QdrantAdapter",
        lambda *args, **kwargs: real_adapter(
            url="https://qdrant.test",
            api_key_file=str(key_file),
            transport=transport,
        ),
    )
    return state


def create_table_and_row(client):
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/检索/Qdrant",
            "name": "Qdrant测试",
            "columns": [{"name": "body", "type": "string", "nullable": False}],
        },
    )
    assert created.status_code == 201, created.text
    table_id = created.json()["data"]["table_id"]
    row = client.post(
        f"/api/v1/tables/{table_id}/rows",
        headers=auth(),
        json={"values": {"body": "fast text fact"}},
    )
    assert row.status_code == 201, row.text
    return table_id, row.json()["data"]["_row_id"]


def test_qdrant_index_and_search_reauthorize_current_row(settings, tmp_path, monkeypatch):
    from tests.test_search import tiny_tokenizer, words

    path, _ = tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "fake")
    state = configure_qdrant(tmp_path, monkeypatch)
    client = TestClient(create_app("xelibrary", settings))
    tid, rid = create_table_and_row(client)
    row = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin"))
    updated = client.patch(
        f"/api/v1/tables/{tid}/rows/{rid}",
        headers=auth("admin", **{"If-Match": row.headers["ETag"]}),
        json={"values": {"body": words(300)}},
    )
    assert updated.status_code == 200

    started = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    job_id = started.json()["data"]["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}", headers=auth("admin")).json()["data"]
    assert job["status"] == "completed"
    assert len(state["points"]) > 1
    point = state["points"][0]
    assert len(point["vector"]["fast_text"]) == 256
    assert point["payload"]["model_revision"] == "d100m-test-v1"
    assert point["payload"]["chunker_version"] == search.CHUNKER_VERSION

    found = client.post("/api/v1/search/semantic", headers=auth(), json={"query": "fast", "path": "/**"})
    assert found.status_code == 200
    assert found.json()["meta"]["index_backend"] == "qdrant_fast_text"
    assert found.json()["data"][0]["row_id"] == rid
    assert found.json()["data"][0]["approximate"] is True
    assert found.json()["data"][0]["snippet"] == words(300)[:240]
    assert state["query_limits"][-1] == 200

    # Rebuild retries reuse deterministic point IDs rather than appending copies.
    original_ids = {point["id"] for point in state["points"]}
    again = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    assert client.get(f"/api/v1/jobs/{again.json()['data']['job_id']}", headers=auth("admin")).json()["data"][
        "status"
    ] == "completed"
    assert {point["id"] for point in state["points"]} == original_ids
    assert len(state["points"]) == len(original_ids)

    # The ANN request overfetches candidates, returns one row after chunk
    # aggregation, and marks the response partial if the candidate cap is hit.
    stale_points = []
    for ordinal in range(100):
        stale_points.append(
            {
                "id": f"stale-{ordinal}",
                "vector": {"fast_text": [0.1] * 256},
                "payload": {
                    "table_id": tid,
                    "row_id": rid,
                    "row_revision": 0,
                    "chunk_id": f"stale-{ordinal}",
                    "column": "body",
                    "path": "/检索/Qdrant",
                    "model_revision": "d100m-test-v1",
                    "chunker_version": search.CHUNKER_VERSION,
                },
            }
        )
    state["points"].extend(stale_points)
    overfetched = client.post(
        "/api/v1/search/semantic",
        headers=auth(),
        json={"query": "fast", "path": "/**", "limit": 5},
    )
    assert overfetched.status_code == 200
    assert len(overfetched.json()["data"]) == 1
    assert overfetched.json()["meta"]["partial"] is True
    assert state["query_limits"][-1] == 100
    state["points"] = [point for point in state["points"] if not point["id"].startswith("stale-")]

    filtered = client.post(
        "/api/v1/search/semantic",
        headers=auth(),
        json={"query": "fast", "path": "/检索/**", "filters": {"body": "different"}},
    )
    assert filtered.json()["data"] == []
    wrong_path = client.post(
        "/api/v1/search/semantic", headers=auth(), json={"query": "fast", "path": "/no-match/**"}
    )
    assert wrong_path.json()["data"] == []

    current_table = client.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"]
    moved = client.post(
        f"/api/v1/tables/{tid}/move",
        headers=auth("admin", **{"If-Match": current_table}),
        json={"path": "/relocated/Qdrant"},
    )
    assert moved.status_code == 200
    old_path = client.post(
        "/api/v1/search/semantic", headers=auth(), json={"query": "fast", "path": "/检索/**"}
    )
    assert old_path.json()["data"] == []
    assert state["points"] == []
    new_path = client.post(
        "/api/v1/search/semantic", headers=auth(), json={"query": "fast", "path": "/relocated/**"}
    )
    assert new_path.json()["data"] == []
    assert new_path.json()["meta"]["partial"] is False
    reindexed = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    assert client.get(
        f"/api/v1/jobs/{reindexed.json()['data']['job_id']}", headers=auth("admin")
    ).json()["data"]["status"] == "completed"
    new_path = client.post(
        "/api/v1/search/semantic", headers=auth(), json={"query": "fast", "path": "/relocated/**"}
    )
    assert len(new_path.json()["data"]) == 1

    put_acl(client, "row", f"{tid}:{rid}", {})
    denied = client.post("/api/v1/search/semantic", headers=auth("bob"), json={"query": "fast"})
    assert denied.json()["data"] == []

    # A fresh row revision makes the existing Qdrant point stale and invisible.
    current_row = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin"))
    updated = client.patch(
        f"/api/v1/tables/{tid}/rows/{rid}",
        headers=auth("admin", **{"If-Match": current_row.headers["ETag"]}),
        json={"values": {"body": "new fact"}},
    )
    assert updated.status_code == 200
    stale = client.post("/api/v1/search/semantic", headers=auth("admin"), json={"query": "fast"})
    assert stale.json()["data"] == []


def test_qdrant_row_lifecycle_cleanup_is_durable_and_failure_fails_closed(settings, tmp_path, monkeypatch):
    from tests.test_search import tiny_tokenizer

    path, _ = tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "fake")
    state = configure_qdrant(tmp_path, monkeypatch)
    client = TestClient(create_app("xelibrary", settings))
    tid, rid = create_table_and_row(client)
    started = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    job = client.get(f"/api/v1/jobs/{started.json()['data']['job_id']}", headers=auth("admin")).json()["data"]
    assert job["status"] == "completed" and len(state["points"]) == 1

    current = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin"))
    state["fail_delete"] = True
    # Adapter is constructed from the current transport factory; a failed remote
    # cleanup rolls the local write back and leaves the source row unchanged.
    removed = client.delete(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin", **{"If-Match": current.headers["ETag"]}))
    assert removed.status_code == 503
    still_live = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin"))
    assert still_live.status_code == 200
    assert still_live.json()["data"]["values"]["body"] == "fast text fact"
    assert len(state["points"]) == 1
    state["fail_delete"] = False

    # Replace the test transport with a healthy Qdrant endpoint, then exercise
    # soft delete, restore, patch/rebuild failure, hard purge, and table cleanup.
    reindex = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    assert client.get(f"/api/v1/jobs/{reindex.json()['data']['job_id']}", headers=auth("admin")).json()["data"][
        "status"
    ] == "completed"
    current = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin"))
    deleted = client.delete(
        f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin", **{"If-Match": current.headers["ETag"]})
    )
    assert deleted.status_code == 200 and state["points"] == []
    restored = client.post(
        f"/api/v1/tables/{tid}/rows/{rid}/restore",
        headers=auth("admin", **{"If-Match": '"revision:2"'}),
        json={"revision": 1},
    )
    assert restored.status_code == 200 and state["points"] == []
    changed = client.patch(
        f"/api/v1/tables/{tid}/rows/{rid}",
        headers=auth("admin", **{"If-Match": restored.headers["ETag"]}),
        json={"values": {"body": "rebuilt after restore"}},
    )
    assert changed.status_code == 200 and state["points"] == []
    state["fail_upsert"] = True
    failed = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    failed_job = client.get(f"/api/v1/jobs/{failed.json()['data']['job_id']}", headers=auth("admin"))
    assert failed_job.json()["data"]["status"] == "failed"
    assert state["points"] == []
    state["fail_upsert"] = False
    failed_row = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth("admin"))
    purged = client.delete(
        f"/api/v1/tables/{tid}/rows/{rid}?purge=true",
        headers=auth("admin", **{"If-Match": failed_row.headers["ETag"]}),
    )
    assert purged.status_code == 200 and state["points"] == []

    # Even unknown remote leftovers are cleared by table scope before the
    # canonical table is removed.
    state["points"].append(
        {
            "id": "orphan",
            "vector": {"fast_text": [0.1] * 256},
            "payload": {"table_id": tid, "row_id": rid},
        }
    )
    schema = client.get(f"/api/v1/tables/{tid}/schema", headers=auth("admin"))
    removed_table = client.delete(
        f"/api/v1/tables/{tid}", headers=auth("admin", **{"If-Match": schema.headers["ETag"]})
    )
    assert removed_table.status_code == 200
    assert state["points"] == []


@pytest.mark.parametrize(
    ("field", "value"),
    (("chunker_version", "chunker-v0"), ("model_revision", "other-model")),
)
def test_qdrant_search_discards_model_and_chunker_mismatch(settings, tmp_path, monkeypatch, field, value):
    path, _ = __import__("tests.test_search", fromlist=["tiny_tokenizer"]).tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "fake")

    def mismatched(points):
        changed = []
        for point in points:
            point = {**point, "payload": dict(point["payload"])}
            point["payload"][field] = value
            changed.append(point)
        return changed

    configure_qdrant(tmp_path, monkeypatch, mutate_query=mismatched)
    client = TestClient(create_app("xelibrary", settings))
    tid, _ = create_table_and_row(client)
    client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    found = client.post("/api/v1/search/semantic", headers=auth(), json={"query": "fast"})
    assert found.json()["data"] == []


@pytest.mark.parametrize(
    "candidate_mutation",
    [
        pytest.param("forged_id", id="point-id-does-not-match-payload"),
        pytest.param("wrong_column", id="column-does-not-match-indexed-chunk"),
        pytest.param("nonfinite_score", id="non-finite-score"),
        pytest.param("invalid_score", id="invalid-score"),
    ],
)
def test_qdrant_search_rejects_forged_candidate_identity_column_and_score(
    settings, tmp_path, monkeypatch, candidate_mutation
):
    path, _ = __import__("tests.test_search", fromlist=["tiny_tokenizer"]).tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "fake")

    def malicious(points):
        selected = [point for point in points if point["payload"]["column"] == "body"]
        changed = []
        for point in selected:
            point = {**point, "payload": dict(point["payload"])}
            # These deliberately dominant candidates must never select another
            # chunk's text or distort ranking for the valid row.
            point["candidate_score"] = 1_000_000.0
            if candidate_mutation == "forged_id":
                point["candidate_id"] = "not-the-payload-chunk-id"
            elif candidate_mutation == "wrong_column":
                point["payload"]["column"] = "secret"
            elif candidate_mutation == "nonfinite_score":
                point["candidate_score"] = float("nan")
            elif candidate_mutation == "invalid_score":
                point["candidate_score"] = "highest"
            changed.append(point)
        return changed

    configure_qdrant(tmp_path, monkeypatch, mutate_query=malicious)
    client = TestClient(create_app("xelibrary", settings))
    created = client.post(
        "/api/v1/tables",
        headers=auth(),
        json={
            "path": "/检索/Candidate integrity",
            "name": "Candidate integrity",
            "columns": [
                {"name": "body", "type": "string", "nullable": False},
                {"name": "secret", "type": "string", "nullable": False},
            ],
        },
    )
    assert created.status_code == 201, created.text
    tid = created.json()["data"]["table_id"]
    row = client.post(
        f"/api/v1/tables/{tid}/rows",
        headers=auth(),
        json={"values": {"body": "ordinary indexed text", "secret": "PRIVATE-SNIPPET"}},
    )
    assert row.status_code == 201, row.text
    indexed = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    assert client.get(f"/api/v1/jobs/{indexed.json()['data']['job_id']}", headers=auth("admin")).json()[
        "data"
    ]["status"] == "completed"

    result = client.post("/api/v1/search/semantic", headers=auth(), json={"query": "ordinary", "limit": 1})
    assert result.status_code == 200, result.text
    assert result.json()["data"] == []
    assert "PRIVATE-SNIPPET" not in result.text


def test_qdrant_write_failure_fails_job_and_does_not_mark_indexed(settings, tmp_path, monkeypatch):
    path, _ = __import__("tests.test_search", fromlist=["tiny_tokenizer"]).tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "fake")
    configure_qdrant(tmp_path, monkeypatch, fail_upsert=True)
    client = TestClient(create_app("xelibrary", settings))
    tid, rid = create_table_and_row(client)
    started = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid})
    job = client.get(f"/api/v1/jobs/{started.json()['data']['job_id']}", headers=auth("admin")).json()["data"]
    assert job["status"] == "failed"
    db_path = settings.data_dir / "tables" / f"{tid}.xl"
    with sqlite3.connect(db_path) as db:
        row = db.execute("SELECT indexed_revision FROM __xl_changes WHERE row_id=?", (rid,)).fetchone()
    assert row == (0,)


def test_qdrant_tls_and_dependency_failures_do_not_fall_back_to_bootstrap(tmp_path, monkeypatch):
    key_file = tmp_path / "key"
    key_file.write_text("secret")
    monkeypatch.setenv("QDRANT_URL", "http://qdrant.invalid")
    monkeypatch.setenv("QDRANT_API_KEY_FILE", str(key_file))
    try:
        search.QdrantAdapter()
    except Exception as exc:
        assert getattr(exc, "code", "") == "QDRANT_TLS_REQUIRED"
    else:
        raise AssertionError("HTTP Qdrant endpoint was accepted")


def test_live_acceptance_script_uses_and_cleans_only_its_temporary_collection(monkeypatch, capsys):
    from scripts import accept_qdrant_fast_text as acceptance

    instances = []

    class FakeAcceptanceAdapter:
        def __init__(self, *, collection):
            self.collection = collection
            self.exists = False
            self.points = []
            self.deleted = False
            instances.append(self)

        def collection_info(self):
            return {"name": self.collection} if self.exists else None

        def create_collection(self):
            self.exists = True

        def upsert(self, points):
            self.points = points

        def query(self, vector, limit, model_revision):
            point = self.points[0]
            return [{"id": point["id"], "score": 1.0}]

        def delete_collection(self):
            self.exists = False
            self.deleted = True

        def close(self):
            pass

    monkeypatch.setattr(acceptance, "QdrantAdapter", FakeAcceptanceAdapter)
    acceptance.main()
    assert len(instances) == 1
    assert instances[0].deleted and not instances[0].exists
    assert instances[0].collection.startswith("xeonolab_accept_fast_text_")
    assert json.loads(capsys.readouterr().out)["result"] == "passed"


def test_live_acceptance_script_cleans_collection_when_validation_fails(monkeypatch):
    from scripts import accept_qdrant_fast_text as acceptance

    adapters = []

    class FailingQueryAdapter:
        def __init__(self, *, collection):
            self.exists = False
            self.deleted = False
            self.closed = False
            adapters.append(self)

        def collection_info(self):
            return {"exists": True} if self.exists else None

        def create_collection(self):
            self.exists = True

        def upsert(self, points):
            pass

        def query(self, vector, limit, model_revision):
            return []

        def delete_collection(self):
            self.exists = False
            self.deleted = True

        def close(self):
            self.closed = True

    monkeypatch.setattr(acceptance, "QdrantAdapter", FailingQueryAdapter)
    with pytest.raises(RuntimeError, match="did not return"):
        acceptance.main()
    assert len(adapters) == 1
    assert adapters[0].deleted and adapters[0].closed and not adapters[0].exists
