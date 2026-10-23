"""Real route and HTTP adapter, with synthetic embedding/Qdrant transports."""

import json

import httpx
from httpx import Client as HTTPClient

from xeonolab.quality_provider import QUALITY_MODEL, QualityClient

from .test_quality_search import find
from .test_quality_search import quality_library as quality_library  # Fixture registration.


def test_quality_route_uses_http_space_contract_and_cache_and_fails_closed(quality_library):
    caller, _tid, rows, _qdrant, _calls, _control = quality_library
    requests = []
    state = {"revision": "synthetic-http-v1", "status": 200}

    def handle(request):
        assert request.headers["Authorization"] == "Bearer synthetic-http-token"
        if state["status"] != 200:
            return httpx.Response(state["status"], text="private-upstream-detail")
        if request.url.path == "/v1/models":
            requests.append(("descriptor", state["revision"]))
            return httpx.Response(200, json={"data": [{
                "id": QUALITY_MODEL, "alias": "quality", "status": "ready",
                "model_revision": state["revision"], "dimensions": 2,
                "preprocessing_revision": "synthetic-http-processing-v1",
                "max_batch_size": 2, "max_tokens": 8192, "normalized": False,
            }]})
        assert request.url.path == "/v1/embeddings"
        body = json.loads(request.read())
        assert body["model"] == "quality" and body["model_revision"] == state["revision"]
        assert body["dimensions"] == 2
        assert body["preprocessing_revision"] == "synthetic-http-processing-v1"
        purpose, texts = body["purpose"], body["input"]
        requests.append((purpose, texts))
        # Deliberately scaled and reverse-indexed: actual adapter must normalize
        # and restore batch order before this real pipeline computes cosine.
        vectors = [[5, 0] if purpose == "query" or "beta" in text else [0, 3] for text in texts]
        return httpx.Response(200, json={
            "model": QUALITY_MODEL, "model_revision": state["revision"], "dimensions": 2,
            "preprocessing_revision": "synthetic-http-processing-v1",
            "data": [{"index": i, "embedding": vectors[i]} for i in reversed(range(len(texts)))],
            "usage": {"input_tokens": len(texts)},
        })

    def factory():
        adapter = QualityClient("https://embedding.invalid", token_provider=lambda: "synthetic-http-token")
        # The existing fast fixture patches global httpx.Client. This instance
        # uses the real captured HTTP class and the explicit synthetic transport.
        adapter.client = HTTPClient(transport=httpx.MockTransport(handle), trust_env=False)
        return adapter

    caller.app.state.quality_pipeline.client_factory = factory
    first = find(caller, limit=1)
    assert first.status_code == 200, first.text
    assert first.json()["data"][0]["row_id"] == rows["beta"]
    assert first.json()["meta"]["quality_model_revision"] == "synthetic-http-v1"
    assert first.json()["meta"]["encoded_chunks"] == 2
    assert len([item for item in requests if item[0] == "document"]) == 1
    assert find(caller).json()["meta"]["cache_hits"] == 2
    assert len([item for item in requests if item[0] == "query"]) == 2
    assert len([item for item in requests if item[0] == "document"]) == 1
    state["revision"] = "synthetic-http-v2"
    changed = find(caller)
    assert changed.status_code == 200
    assert changed.json()["meta"]["cache_hits"] == 0
    assert changed.json()["meta"]["encoded_chunks"] == 2
    state["status"] = 503
    unavailable = find(caller)
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "QWEN_UNAVAILABLE"
    assert "private-upstream-detail" not in unavailable.text
    assert "data" not in unavailable.json()
