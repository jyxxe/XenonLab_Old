"""Synthetic HTTP contracts; no model weights or real inference are used."""

import json
import math

import httpx
import pytest

from xeonolab.common import APIError
from xeonolab.quality_provider import QUALITY_MODEL, QualityClient, QualityDescriptor


def model(**changes):
    return {
        "id": QUALITY_MODEL, "alias": "quality", "status": "ready",
        "model_revision": "synthetic-snapshot-v1", "dimensions": 2,
        "preprocessing_revision": "synthetic-preprocessing-v1",
        "max_batch_size": 2, "max_tokens": 8192, "normalized": False, **changes,
    }


def output(rows=None, **changes):
    return {
        "model": QUALITY_MODEL, "model_revision": "synthetic-snapshot-v1", "dimensions": 2,
        "preprocessing_revision": "synthetic-preprocessing-v1",
        "data": rows if rows is not None else [{"index": 0, "embedding": [3, 4]}], **changes,
    }


def client(handler):
    return QualityClient(
        "https://embedding.invalid", token_provider=lambda: "synthetic-provider-token",
        transport=httpx.MockTransport(handler),
    )


def assert_error(code, call, status=503):
    with pytest.raises(APIError) as caught:
        call()
    assert caught.value.code == code
    assert caught.value.status == status
    assert "private-upstream-detail" not in caught.value.message


def test_quality_adapter_uses_service_api_pinned_space_purpose_and_index_order():
    requests = []

    def handle(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer synthetic-provider-token"
        if request.url.path == "/v1/models":
            assert request.method == "GET"
            return httpx.Response(200, json={"data": [model()]})
        assert request.method == "POST" and request.url.path == "/v1/embeddings"
        assert json.loads(request.read()) == {
            "model": "quality", "input": ["first", "second"], "purpose": "query",
            "model_revision": "synthetic-snapshot-v1", "dimensions": 2,
            "preprocessing_revision": "synthetic-preprocessing-v1",
        }
        return httpx.Response(200, json=output([
            {"index": 1, "embedding": [0, -10]}, {"index": 0, "embedding": [3, 4]},
        ]))

    with client(handle) as adapter:
        descriptor = adapter.descriptor()
        vectors = adapter.encode(["first", "second"], descriptor, purpose="query")
        assert descriptor.space_key == (
            QUALITY_MODEL, "synthetic-snapshot-v1", 2, "synthetic-preprocessing-v1",
        )
        assert vectors == [[0.6, 0.8], [0, -1]]
        assert all(math.hypot(*row) == pytest.approx(1) for row in vectors)
    assert adapter.client is None and len(requests) == 2


@pytest.mark.parametrize("change", [
    {"dimensions": True}, {"dimensions": 0}, {"dimensions": 2049},
    {"model_revision": ""}, {"preprocessing_revision": " "},
    {"max_batch_size": 0}, {"max_tokens": 32769}, {"normalized": "true"},
])
def test_quality_descriptor_rejects_invalid_space_and_limits(change):
    assert_error("QWEN_PROTOCOL_ERROR", lambda: QualityDescriptor.from_model(model(**change)))


@pytest.mark.parametrize("rows,code", [
    ([], "QWEN_UNAVAILABLE"), ([model(status="not_configured")], "QWEN_UNAVAILABLE"),
    ([model(), model()], "QWEN_PROTOCOL_ERROR"),
])
def test_quality_descriptor_never_infers_readiness_from_fast_model(rows, code):
    with client(lambda _r: httpx.Response(200, json={"data": [{"id": "D100M-Embedding"}, *rows]})) as adapter:
        assert_error(code, adapter.descriptor)


@pytest.mark.parametrize("changes", [
    {"model": "D100M-Embedding"}, {"model_revision": "new-revision"},
    {"dimensions": 3}, {"dimensions": True}, {"preprocessing_revision": "new-processing"},
])
def test_quality_encoding_rejects_space_change_after_descriptor(changes):
    descriptor = QualityDescriptor.from_model(model())
    with client(lambda _r: httpx.Response(200, json=output(**changes))) as adapter:
        assert_error("QWEN_PROTOCOL_ERROR", lambda: adapter.encode(["query"], descriptor))


@pytest.mark.parametrize("rows", [
    [], [{"index": True, "embedding": [1, 0]}], [{"index": 1, "embedding": [1, 0]}],
    [{"index": 0, "embedding": [0, 0]}], [{"index": 0, "embedding": [1]}],
    [{"index": 0, "embedding": [True, 1]}], [{"index": 0, "embedding": ["1", 0]}],
    [{"index": 0, "embedding": [float("nan"), 1]}],
    [{"index": 0, "embedding": [float("inf"), 1]}],
])
def test_quality_encoding_rejects_nonfinite_zero_mismatched_or_missing_vectors(rows):
    # allow_nan encodes deliberately malformed provider output, never product input.
    payload = json.dumps(output(rows), allow_nan=True)
    descriptor = QualityDescriptor.from_model(model())
    with client(lambda _r: httpx.Response(200, content=payload)) as adapter:
        assert_error("QWEN_PROTOCOL_ERROR", lambda: adapter.encode(["query"], descriptor))


def test_quality_encoding_rejects_duplicate_indices_even_with_correct_batch_size():
    payload = output([{"index": 0, "embedding": [1, 0]}, {"index": 0, "embedding": [0, 1]}])
    with client(lambda _r: httpx.Response(200, json=payload)) as adapter:
        assert_error("QWEN_PROTOCOL_ERROR", lambda: adapter.encode(["first", "second"],
                                                                  QualityDescriptor.from_model(model())))


@pytest.mark.parametrize("batch,purpose", [
    ([], "document"), (["a", "b", "c"], "document"), ([""], "query"),
    ([" "], "document"), ([123], "document"), (["x" * 32769], "document"), (["x"], "unknown"),
])
def test_quality_invalid_local_batch_fails_before_auth_or_network(batch, purpose):
    def unexpected(_request):
        pytest.fail("invalid local input reached provider")

    with client(unexpected) as adapter:
        assert_error("QUALITY_INPUT_REJECTED", lambda: adapter.encode(batch, QualityDescriptor.from_model(model()),
                                                                      purpose=purpose), status=422)


@pytest.mark.parametrize("status,code", [
    (429, "QUALITY_BUSY"), (422, "QUALITY_INPUT_REJECTED"),
    (401, "QWEN_UNAVAILABLE"), (503, "QWEN_UNAVAILABLE"), (302, "QWEN_UNAVAILABLE"),
])
def test_quality_http_errors_are_bounded_and_do_not_echo_body_or_redirect(status, code):
    with client(lambda _r: httpx.Response(status, text="private-upstream-detail")) as adapter:
        assert_error(code, adapter.descriptor, status=status if status in (422, 429) else 503)


def test_quality_timeout_invalid_json_and_response_bound_are_explicit(monkeypatch):
    def timeout(request):
        raise httpx.ReadTimeout("private-upstream-detail", request=request)

    with client(timeout) as adapter:
        assert_error("QWEN_UNAVAILABLE", adapter.descriptor)
    with client(lambda _r: httpx.Response(200, content=b"not json")) as adapter:
        assert_error("QWEN_PROTOCOL_ERROR", adapter.descriptor)
    monkeypatch.setattr("xeonolab.quality_provider.MAX_RESPONSE_BYTES", 10)
    with client(lambda _r: httpx.Response(200, content=b" " * 11)) as adapter:
        assert_error("QWEN_PROTOCOL_ERROR", adapter.descriptor)


def test_quality_missing_configuration_never_requests_auth_or_network(monkeypatch):
    monkeypatch.delenv("EMBEDDING_URL", raising=False)
    with QualityClient(token_provider=lambda: pytest.fail("missing endpoint requested credentials")) as adapter:
        assert_error("QWEN_UNAVAILABLE", adapter.descriptor)


def test_quality_service_token_is_obtained_again_for_each_request():
    issued, seen = [], []

    def token():
        issued.append(len(issued) + 1)
        return "synthetic-token-" + str(issued[-1])

    def handle(request):
        seen.append(request.headers["Authorization"])
        return httpx.Response(200, json={"data": [model()]} if request.url.path == "/v1/models" else output())

    with QualityClient("https://embedding.invalid", token_provider=token,
                       transport=httpx.MockTransport(handle)) as adapter:
        descriptor = adapter.descriptor()
        adapter.encode(["query"], descriptor)
    assert seen == ["Bearer synthetic-token-1", "Bearer synthetic-token-2"]


def test_quality_absolute_budget_limits_next_request_and_stream(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("xeonolab.quality_provider.time.monotonic", lambda: now[0])
    sent = []

    def handle(request):
        sent.append(request)
        assert request.extensions["timeout"]["read"] == 5
        now[0] = 106.0
        return httpx.Response(200, json={"data": [model()]})

    with client(handle) as adapter:
        adapter.deadline = 105.0
        assert_error("QUALITY_TIMEOUT", adapter.descriptor)
        # An expired request budget fails before a second provider call.
        assert_error("QUALITY_TIMEOUT", adapter.descriptor)
        assert len(sent) == 1
