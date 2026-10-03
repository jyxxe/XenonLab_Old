"""Normal synthetic images/provider/Qdrant REST; these tests never load Qwen."""

import io
import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from xeonolab import image_search
from xeonolab.common import APIError
from xeonolab.services import create_app
from xeonolab.vision_provider import QWEN_MODEL, VisionProvider, VisionSpace, validate_image
from xeonolab.vision_qdrant import VisionAdapter

from .conftest import auth, put_acl


def picture(fmt="PNG"):
    buffer = io.BytesIO()
    Image.new("RGB", (16, 12), (20, 30, 40)).save(buffer, format=fmt)
    return buffer.getvalue()


@pytest.fixture
def synthetic(settings, tmp_path, monkeypatch):
    """Use actual facade HTTP and Qdrant REST clients over controlled transports."""
    monkeypatch.setenv("EMBEDDING_URL", "https://synthetic-embedding.test")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "synthetic-service-token")
    monkeypatch.delenv("EMBEDDING_CA_FILE", raising=False)
    for key in ("QDRANT_URL", "QDRANT_API_KEY_FILE", "QDRANT_CA_FILE", "D100M_MODEL_PATH"):
        monkeypatch.delenv(key, raising=False)
    settings.rate_limit_policies["embedding-quality"]["requests"] = 100
    key = tmp_path / "synthetic-qdrant.key"
    key.write_text("synthetic-key")
    state = {
        "space": VisionSpace(model_revision="synthetic-r1", dimensions=3, preprocessing_revision="synthetic-p1"),
        "ready": True, "provider_error": 0, "collections": {}, "calls": [], "encodes": [], "fail_delete": False,
        "after_encode": None,
    }

    def embedding(request):
        assert request.headers["Authorization"] == "Bearer synthetic-service-token"
        space = state["space"]
        state["calls"].append(request.url.path)
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": QWEN_MODEL, "alias": "quality", "status": "ready" if state["ready"] else "not_configured",
                **space.model_dump(exclude={"model_id"}), "max_batch_size": 1, "max_tokens": 8192,
                "image_max_bytes": state.get("image_max_bytes", 16 * 1024 * 1024), "image_max_pixels": 16_000_000,
            }]})
        state["encodes"].append((request.url.path, request.read()))
        if state["provider_error"]:
            return httpx.Response(state["provider_error"], json={"error": {"code": "synthetic-provider-error"}})
        if state["after_encode"]:
            callback, state["after_encode"] = state["after_encode"], None
            callback()
        return httpx.Response(200, json={
            "model": QWEN_MODEL, **space.model_dump(exclude={"model_id"}),
            "data": [{"index": 0, "object": "embedding", "embedding": [1.0, 0.0, 0.0]}],
            "usage": {"input_tokens": 4, "total_tokens": 4},
        })

    def qdrant(request):
        assert request.headers["api-key"] == "synthetic-key"
        collection = request.url.path.split("/")[2]
        assert collection.startswith("vision_")
        collections = state["collections"]
        if request.url.path == "/collections/" + collection:
            if request.method == "GET":
                if collection not in collections:
                    return httpx.Response(404)
                return httpx.Response(200, json={"status": "ok", "result": {
                    "config": {"params": {"vectors": collections[collection]["vectors"]}},
                }})
            assert request.method == "PUT"
            body = json.loads(request.read())
            collections[collection] = {"vectors": body["vectors"], "points": {}}
            return httpx.Response(200, json={"status": "ok", "result": True})
        points = collections[collection]["points"]
        body = json.loads(request.read())
        if request.method == "PUT":
            for point in body["points"]:
                assert set(point["vector"]) == {"vision"}
                points[point["id"]] = point
        elif request.url.path.endswith("/query"):
            assert body["using"] == "vision"
            assert body["limit"] <= 1000
            matches = [p for p in points.values() if p["payload"]["space_key"] == body["filter"]["must"][0]["match"]["value"]]
            return httpx.Response(200, json={"status": "ok", "result": {"points": [
                {"id": p["id"], "payload": p["payload"], "score": 0.9} for p in matches[:body["limit"]]
            ]}})
        elif request.url.path.endswith("/delete"):
            if state["fail_delete"]:
                return httpx.Response(503)
            ids = body.get("points")
            if ids is None:
                conditions = body["filter"]["must"]
                ids = [point_id for point_id, point in points.items() if all(
                    point["payload"].get(item["key"]) == item["match"]["value"] for item in conditions
                )]
            for point_id in ids:
                points.pop(point_id, None)
        else:
            raise AssertionError(request.url)
        return httpx.Response(200, json={"status": "ok", "result": {"status": "completed"}})

    monkeypatch.setattr(image_search, "VisionProvider", lambda: VisionProvider(transport=httpx.MockTransport(embedding)))
    monkeypatch.setattr(image_search, "VisionAdapter", lambda space: VisionAdapter(
        space, url="https://synthetic-qdrant.test", api_key_file=str(key), transport=httpx.MockTransport(qdrant),
    ))
    client = TestClient(create_app("xelibrary", settings))
    table = client.post("/api/v1/tables", headers=auth(), json={
        "path": "/synthetic/images", "name": "synthetic images",
        "columns": [{"name": "body", "type": "string", "nullable": False}],
    })
    assert table.status_code == 201, table.text
    tid = table.json()["data"]["table_id"]
    row = client.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"body": "current image fact"}})
    assert row.status_code == 201
    rid = row.json()["data"]["_row_id"]
    attachment = client.post("/api/v1/attachments", headers=auth(), params={"table_id": tid, "row_id": rid},
                             files={"upload": ("synthetic.png", picture(), "image/png")})
    assert attachment.status_code == 201, attachment.text
    aid = attachment.json()["data"]["attachment_id"]
    return client, state, tid, rid, aid


def index(client, tid):
    result = client.post("/api/v1/index/vision/reindex", headers=auth("admin"), json={"table_id": tid})
    assert result.status_code == 202, result.text
    job_id = result.json()["data"]["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}", headers=auth("admin")).json()["data"]
    assert job["status"] == "completed", job
    assert job["kind"] == "vision"
    return job


def search(client, **fields):
    result = client.post("/api/v1/search/image", headers=auth(), data={"text": "normal image query", **fields})
    assert result.status_code == 200, result.text
    return result.json()


def points(state):
    return [p for collection in state["collections"].values() for p in collection["points"].values()]


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "WEBP"])
def test_normal_single_frame_image_validation(fmt):
    assert validate_image(picture(fmt)) == Image.MIME[fmt]


def test_image_index_real_http_contract_and_normal_queries(synthetic):
    client, state, tid, rid, aid = synthetic
    assert search(client)["meta"]["missing_embeddings"] == 1
    before_row = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).json()["data"]
    before_table_etag = client.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"]
    job = index(client, tid)
    assert client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).json()["data"] == before_row
    assert client.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"] == before_table_etag
    assert job["indexed"] == 1
    assert len(points(state)) == 1
    result = search(client)
    assert result["meta"]["partial"] is False
    assert result["meta"]["space"]["dimensions"] == 3  # Explicitly synthetic; never D100M or real Qwen.
    hit = result["data"][0]
    assert hit["row_id"] == rid and hit["snippet"] == "current image fact"
    assert hit["score_type"] == "qwen3vl_cosine"
    assert hit["matched_attachments"][0]["attachment_id"] == aid
    image_query = client.post("/api/v1/search/image", headers=auth(), files={"upload": ("q.png", picture(), "image/png")})
    assert image_query.status_code == 200 and image_query.json()["data"][0]["row_id"] == rid
    attachment_query = client.post("/api/v1/search/image", headers=auth(), data={"table_id": tid, "attachment_id": aid})
    assert attachment_query.status_code == 200 and attachment_query.json()["data"][0]["row_id"] == rid
    text_body = json.loads(next(raw for path, raw in state["encodes"] if path == "/v1/embeddings"))
    assert text_body["purpose"] == "query" and text_body["model"] == "quality"
    assert text_body["model_revision"] == "synthetic-r1"
    multipart = [raw for path, raw in state["encodes"] if path == "/api/v1/embed/image"]
    assert any(b'\r\ndocument\r\n' in raw for raw in multipart)
    assert any(b'\r\nquery\r\n' in raw for raw in multipart)
    assert search(client, path="/elsewhere/**")["data"] == []
    assert search(client, filters=json.dumps({"body": "different"}))["data"] == []
    assert "xelibrary_vision_requests_total" in client.get("/metrics").text


def test_reindex_idempotent_and_old_space_cleanup(synthetic):
    client, state, tid, _, _ = synthetic
    index(client, tid)
    original = points(state)[0]["id"]
    index(client, tid)
    assert len(points(state)) == 1 and points(state)[0]["id"] == original
    old_collection = state["space"].collection()
    state["space"] = VisionSpace(model_revision="synthetic-r2", dimensions=3, preprocessing_revision="synthetic-p2")
    assert search(client)["data"] == []
    assert search(client)["meta"]["missing_embeddings"] == 1
    index(client, tid)
    assert not state["collections"][old_collection]["points"]
    assert len(points(state)) == 1 and points(state)[0]["id"] != original
    assert len(search(client)["data"]) == 1


def test_binding_unbind_rebind_and_delete_failure_rolls_back(synthetic):
    client, state, tid, rid, aid = synthetic
    index(client, tid)
    before = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).json()["data"]["_revision"]
    state["fail_delete"] = True
    result = client.delete(f"/api/v1/tables/{tid}/rows/{rid}/attachments/{aid}", headers=auth())
    assert result.status_code == 503
    assert client.get(f"/api/v1/attachments/{aid}", params={"table_id": tid}, headers=auth()).status_code == 200
    assert len(search(client)["data"]) == 1
    state["fail_delete"] = False
    assert client.delete(f"/api/v1/tables/{tid}/rows/{rid}/attachments/{aid}", headers=auth()).status_code == 200
    assert not points(state) and search(client)["data"] == []
    after = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).json()["data"]["_revision"]
    assert before == after
    upload = client.post("/api/v1/attachments", headers=auth(), params={"table_id": tid, "row_id": rid},
                         files={"upload": ("new.png", picture(), "image/png")})
    assert upload.status_code == 201
    assert search(client)["meta"]["missing_embeddings"] == 1
    index(client, tid)
    assert len(search(client)["data"]) == 1


def test_current_acl_filters_candidates_and_attachment_before_provider(synthetic):
    client, state, tid, rid, aid = synthetic
    index(client, tid)
    assert put_acl(client, "table", tid, {"alice": ["read", "write", "attach"], "bob": ["read"]}).status_code == 200
    grants = {"alice": ["read", "write", "attach"], "bob": []}
    assert put_acl(client, "row", f"{tid}:{rid}", grants).status_code == 200
    result = client.post("/api/v1/search/image", headers=auth("bob"), data={"text": "normal"})
    assert result.status_code == 200 and result.json()["data"] == []
    assert result.json()["meta"]["missing_embeddings"] == 0
    before = len(state["calls"])
    denied = client.post("/api/v1/search/image", headers=auth("bob"), data={"table_id": tid, "attachment_id": aid})
    assert denied.status_code == 403
    assert len(state["calls"]) == before
    grants["bob"] = ["read"]
    assert put_acl(client, "row", f"{tid}:{rid}", grants).status_code == 200
    result = client.post("/api/v1/search/image", headers=auth("bob"), data={"text": "normal"})
    assert len(result.json()["data"]) == 1


def test_normal_update_soft_delete_restore_purge_cleanup(synthetic):
    client, state, tid, rid, _ = synthetic
    index(client, tid)
    row_path = f"/api/v1/tables/{tid}/rows/{rid}"
    row = client.get(row_path, headers=auth())
    update = client.patch(row_path, headers=auth(**{"If-Match": row.headers["ETag"]}), json={"values": {"body": "updated fact"}})
    assert update.status_code == 200 and not points(state)
    assert search(client)["meta"]["missing_embeddings"] == 1
    index(client, tid)
    row = client.get(row_path, headers=auth())
    deleted = client.delete(row_path, headers=auth(**{"If-Match": row.headers["ETag"]}))
    assert deleted.status_code == 200 and not points(state)
    assert search(client)["meta"]["missing_embeddings"] == 0
    deleted_revision = update.json()["data"]["_revision"] + 1
    restored = client.post(row_path + "/restore", headers=auth(**{"If-Match": f'"revision:{deleted_revision}"'}), json={"revision": 1})
    assert restored.status_code == 200, restored.text
    index(client, tid)
    latest = client.get(row_path, headers=auth())
    purge = client.delete(row_path + "?purge=true", headers=auth("admin", **{"If-Match": latest.headers["ETag"]}))
    assert purge.status_code == 200 and not points(state)
    assert search(client)["data"] == []


def test_normal_binding_change_during_encoding_cannot_publish(synthetic):
    client, state, tid, rid, aid = synthetic
    state["after_encode"] = lambda: client.delete(
        f"/api/v1/tables/{tid}/rows/{rid}/attachments/{aid}", headers=auth(),
    )
    job = index(client, tid)
    assert job["indexed"] == 0 and job["skipped"] == 1
    assert not points(state)
    assert search(client)["data"] == []


@pytest.mark.parametrize("status,code", [(429, "EMBEDDING_BUSY"), (503, "EMBEDDING_UNAVAILABLE")])
def test_normal_provider_unavailable_and_busy(synthetic, status, code):
    client, state, _, _, _ = synthetic
    state["provider_error"] = status
    result = client.post("/api/v1/search/image", headers=auth(), data={"text": "normal"})
    assert result.status_code == status
    assert result.json()["error"]["code"] == code


def test_not_ready_model_does_not_create_job_or_fake_results(synthetic):
    client, state, tid, _, _ = synthetic
    state["ready"] = False
    result = client.post("/api/v1/search/image", headers=auth(), data={"text": "normal"})
    assert result.status_code == 503 and result.json()["error"]["code"] == "MODEL_NOT_CONFIGURED"
    result = client.post("/api/v1/index/vision/reindex", headers=auth("admin"), json={"table_id": tid})
    assert result.status_code == 503
    assert not state["encodes"] and not state["collections"]


def test_typed_image_openapi_and_index_permission(synthetic):
    client, _, tid, _, _ = synthetic
    spec = client.app.openapi()
    operation = spec["paths"]["/api/v1/search/image"]["post"]
    assert "multipart/form-data" in operation["requestBody"]["content"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("VisionSearchResponse")
    for status in ("403", "413", "422", "429", "503"):
        assert operation["responses"][status]["content"]["application/json"]["schema"]["$ref"].endswith("VisionErrorResponse")
    assert "202" in spec["paths"]["/api/v1/index/vision/reindex"]["post"]["responses"]
    result = client.post("/api/v1/index/vision/reindex", headers=auth(), json={"table_id": tid})
    assert result.status_code == 403


def test_table_move_rebuild_then_purge_and_empty_table_delete(synthetic):
    client, state, tid, rid, _ = synthetic
    index(client, tid)
    table = client.get(f"/api/v1/tables/{tid}", headers=auth())
    moved = client.post(f"/api/v1/tables/{tid}/move", headers=auth(**{"If-Match": table.headers["ETag"]}),
                        json={"path": "/synthetic/moved"})
    assert moved.status_code == 200, moved.text
    assert not points(state)
    assert search(client, path="/synthetic/images/**")["data"] == []
    index(client, tid)
    assert search(client)["data"][0]["path"] == "/synthetic/moved"
    row = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth())
    purged = client.delete(f"/api/v1/tables/{tid}/rows/{rid}?purge=true",
                          headers=auth("admin", **{"If-Match": row.headers["ETag"]}))
    assert purged.status_code == 200 and not points(state)
    table = client.get(f"/api/v1/tables/{tid}", headers=auth())
    deleted = client.delete(f"/api/v1/tables/{tid}", headers=auth("admin", **{"If-Match": table.headers["ETag"]}))
    assert deleted.status_code == 200, deleted.text
    assert search(client)["data"] == []


def test_multiple_bindings_job_partial_failure_and_retry(synthetic):
    client, state, tid, rid, _ = synthetic
    second = client.post("/api/v1/attachments", headers=auth(), params={"table_id": tid, "row_id": rid},
                         files={"upload": ("synthetic.jpg", picture("JPEG"), "image/jpeg")})
    assert second.status_code == 201
    # A normal provider busy response after one successful document is a
    # durable, explicit failed job; successful current references stay usable.
    state["after_encode"] = lambda: state.update(provider_error=429)
    result = client.post("/api/v1/index/vision/reindex", headers=auth("admin"), json={"table_id": tid})
    assert result.status_code == 202
    job_id = result.json()["data"]["job_id"]
    job = client.get(f"/api/v1/jobs/{job_id}", headers=auth("admin")).json()["data"]
    assert job["status"] == "failed" and job["error_code"] == "EMBEDDING_BUSY"
    assert job["indexed"] == 1 and len(points(state)) == 1
    state["provider_error"] = 0
    assert search(client)["meta"]["missing_embeddings"] == 1
    assert search(client)["meta"]["partial"] is True
    assert index(client, tid)["indexed"] == 2
    assert len(points(state)) == 2 and search(client)["meta"]["partial"] is False


def test_interrupted_reference_publication_cleanup_known_space(synthetic, settings):
    client, state, tid, rid, _ = synthetic
    index(client, tid)
    # Model a legitimate process interruption after remote upsert and before
    # committing the local point reference. Durable known-space metadata must
    # still let the ordinary row mutation remove the derived remote point.
    with sqlite3.connect(settings.data_dir / "tables" / f"{tid}.xl") as db:
        db.execute("DELETE FROM __xl_vision_refs")
    assert len(points(state)) == 1
    assert search(client)["data"] == []
    row = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth())
    updated = client.patch(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth(**{"If-Match": row.headers["ETag"]}),
                           json={"values": {"body": "normal update after interrupted index"}})
    assert updated.status_code == 200 and not points(state)


@pytest.mark.parametrize("fmt", ["JPEG", "WEBP"])
def test_normal_legacy_image_metadata_index(synthetic, fmt):
    client, state, tid, rid, _ = synthetic
    created = client.post("/api/v1/attachments", headers=auth(), params={"table_id": tid, "row_id": rid},
                          files={"upload": ("normal.dat", picture(fmt), Image.MIME[fmt])})
    assert created.status_code == 201
    assert search(client)["meta"]["missing_embeddings"] == 2
    assert index(client, tid)["indexed"] == 2
    assert len(points(state)) == 2
    assert search(client)["meta"]["missing_embeddings"] == 0


def test_long_job_facade_requests_refresh_service_token(synthetic, monkeypatch):
    from xeonolab import vision_provider

    _, state, _, _, _ = synthetic
    tokens = iter(["synthetic-token-before-refresh", "synthetic-token-after-refresh"])
    monkeypatch.setattr(vision_provider, "service_token", lambda prefix: next(tokens))
    observed = []

    def normal_service(request):
        observed.append(request.headers["Authorization"])
        space = state["space"]
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{
                "id": QWEN_MODEL, "alias": "quality", "status": "ready", **space.model_dump(exclude={"model_id"}),
                "max_batch_size": 1, "max_tokens": 8192, "image_max_bytes": 16777216, "image_max_pixels": 16000000,
            }]})
        return httpx.Response(200, json={"model": QWEN_MODEL, **space.model_dump(exclude={"model_id"}),
                                        "data": [{"index": 0, "embedding": [1.0, 0.0, 0.0]}]})

    provider = VisionProvider(transport=httpx.MockTransport(normal_service))
    try:
        space = provider.space()
        assert provider.encode(space, text="normal query") == [1.0, 0.0, 0.0]
    finally:
        provider.close()
    assert observed == ["Bearer synthetic-token-before-refresh", "Bearer synthetic-token-after-refresh"]


def test_normal_oversized_provider_response_is_bounded(synthetic):
    class VerboseProviderStream(httpx.SyncByteStream):
        def __init__(self):
            self.chunks = 0
            self.closed = False

        def __iter__(self):
            # A provider accidentally includes a large debug payload. No network
            # or production service is involved; client must stop consumption.
            for _ in range(80):
                self.chunks += 1
                yield b" " * (64 * 1024)

        def close(self):
            self.closed = True

    stream = VerboseProviderStream()
    provider = VisionProvider(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
    try:
        with pytest.raises(APIError) as error:
            provider.space()
        assert error.value.code == "EMBEDDING_UNAVAILABLE"
    finally:
        provider.close()
    assert stream.chunks == 65 and stream.closed


def test_duplicate_model_descriptors_are_not_selected_ambiguously(synthetic):
    _, state, _, _, _ = synthetic
    descriptor = {"id": QWEN_MODEL, "status": "ready", **state["space"].model_dump(exclude={"model_id"})}
    provider = VisionProvider(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
        "data": [descriptor, descriptor],
    })))
    try:
        with pytest.raises(APIError) as error:
            provider.space()
        assert error.value.code == "EMBEDDING_PROTOCOL_ERROR"
    finally:
        provider.close()


def test_whole_result_set_rechecks_normal_acl_change_after_later_candidate(synthetic, monkeypatch):
    client, state, tid, _, _ = synthetic
    second_row = client.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"body": "second normal fact"}})
    rid2 = second_row.json()["data"]["_row_id"]
    assert client.post("/api/v1/attachments", headers=auth(), params={"table_id": tid, "row_id": rid2},
                       files={"upload": ("second.png", picture(), "image/png")}).status_code == 201
    index(client, tid)
    first_rid = points(state)[0]["payload"]["row_id"]
    original_dump = image_search.VisionHit.model_dump
    observed = 0

    def normal_catalogue_change(hit, *args, **kwargs):
        nonlocal observed
        result = original_dump(hit, *args, **kwargs)
        observed += 1
        if observed == 2:
            # A separate authorized local administrator changes the first row's
            # policy while this request is processing its second current row.
            assert put_acl(client, "row", f"{tid}:{first_rid}", {"alice": []}).status_code == 200
        return result

    monkeypatch.setattr(image_search.VisionHit, "model_dump", normal_catalogue_change)
    result = search(client)
    assert len(result["data"]) == 1 and result["data"][0]["row_id"] != first_rid
    assert result["meta"]["partial"] is True
    assert result["meta"]["coverage_snapshot"] is True


@pytest.mark.parametrize("change,expected", [("unbind", 404), ("permission", 403)])
def test_attachment_source_rechecked_after_normal_worker_wait(synthetic, change, expected):
    client, state, tid, rid, aid = synthetic
    index(client, tid)
    if change == "unbind":
        state["after_encode"] = lambda: client.delete(
            f"/api/v1/tables/{tid}/rows/{rid}/attachments/{aid}", headers=auth(),
        )
    else:
        state["after_encode"] = lambda: put_acl(client, "row", f"{tid}:{rid}", {"alice": []})
    result = client.post("/api/v1/search/image", headers=auth(), data={"table_id": tid, "attachment_id": aid})
    assert result.status_code == expected
    assert "data" not in result.json()


def test_normal_mixed_nonimage_binding_is_skipped(synthetic):
    client, state, tid, rid, _ = synthetic
    created = client.post("/api/v1/attachments", headers=auth(), params={"table_id": tid, "row_id": rid},
                          files={"upload": ("notes.txt", b"ordinary synthetic notes", "text/plain")})
    assert created.status_code == 201
    job = index(client, tid)
    assert job["indexed"] == 1 and job["skipped"] == 1
    assert len(points(state)) == 1
    assert search(client)["meta"]["missing_embeddings"] == 0


def test_normal_resumable_nonimage_above_image_budget_is_skipped(synthetic, monkeypatch):
    from xeonolab import vision_provider

    client, state, tid, rid, _ = synthetic
    content = b"ordinary synthetic notes\n" * 32
    # Small normal data exercises the real resumable attachment path; no large files.
    session = client.post("/api/v1/uploads", headers=auth(), json={
        "table_id": tid, "row_id": rid, "filename": "notes.txt", "size": len(content),
    })
    assert session.status_code == 201, session.text
    upload_id = session.json()["data"]["upload_id"]
    uploaded = client.put(f"/api/v1/uploads/{upload_id}/parts/1", headers=auth(), content=content)
    assert uploaded.status_code == 200, uploaded.text
    completed = client.post(f"/api/v1/uploads/{upload_id}/complete", headers=auth())
    assert completed.status_code == 200, completed.text
    monkeypatch.setattr(vision_provider, "IMAGE_MAX_BYTES", 128)
    state["image_max_bytes"] = 128
    assert len(picture()) <= 128 < len(content)
    before_row = client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).json()["data"]
    before_etag = client.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"]
    job = index(client, tid)
    assert job["indexed"] == 1 and job["skipped"] == 1
    assert len(points(state)) == 1 and len(state["encodes"]) == 1
    assert client.get(f"/api/v1/tables/{tid}/rows/{rid}", headers=auth()).json()["data"] == before_row
    assert client.get(f"/api/v1/tables/{tid}", headers=auth()).headers["ETag"] == before_etag
    assert client.get(f"/api/v1/attachments/{completed.json()['data']['attachment_id']}/content",
                      params={"table_id": tid}, headers=auth()).content == content


def test_normal_image_above_image_budget_still_fails_explicitly(synthetic, monkeypatch):
    from xeonolab import vision_provider

    client, state, tid, _, _ = synthetic
    monkeypatch.setattr(vision_provider, "IMAGE_MAX_BYTES", len(picture()) - 1)
    state["image_max_bytes"] = len(picture()) - 1
    created = client.post("/api/v1/index/vision/reindex", headers=auth("admin"), json={"table_id": tid})
    assert created.status_code == 202, created.text
    job = client.get(f"/api/v1/jobs/{created.json()['data']['job_id']}", headers=auth("admin")).json()["data"]
    assert job["status"] == "failed" and job["error_code"] == "UPLOAD_TOO_LARGE"
    assert job["indexed"] == 0 and job["skipped"] == 0
    assert not points(state) and not state["encodes"]
