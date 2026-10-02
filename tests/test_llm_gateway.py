import asyncio
import json
import logging
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from starlette.requests import Request

from xeonolab.services import create_app

from .conftest import auth


def test_model_alias_maps_only_on_server(settings, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    monkeypatch.setenv("LLM_API_KEY", "unit-test-provider-key")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "~deepseek/deepseek-flash-latest")

    class Result:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "OK"}}]}

    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, headers, json):
            assert url == "https://api.jyxxe.com/v1/chat/completions"
            assert headers["Authorization"] == "Bearer unit-test-provider-key"
            assert json["model"] == "~deepseek/deepseek-flash-latest"
            return Result()

    monkeypatch.setattr("xeonolab.services.httpx.AsyncClient", Provider)
    client = TestClient(create_app("llm-gateway", settings))
    result = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "deepseek-flash", "messages": [{"role": "user", "content": "hello"}]},
    )
    assert result.status_code == 200
    assert (
        client.post("/v1/chat/completions", headers=auth(), json={"model": "arbitrary-model"}).status_code
        == 422
    )
    assert client.post("/v1/chat/completions", headers=auth(), json=[]).status_code == 422


class ProviderStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


class StreamingProvider:
    instances = []

    def __init__(self, *, timeout, trust_env, verify):
        assert trust_env is False
        assert timeout.connect == 10
        assert timeout.read == 120
        assert timeout.write == 30
        assert timeout.pool == 10
        self.closed = False
        self.sent_request = None
        self.instances.append(self)

    def build_request(self, method, url, *, headers, json):
        return httpx.Request(method, url, headers=headers, json=json)

    async def send(self, request, stream=False):
        assert stream is True
        self.sent_request = request
        return self.response

    async def aclose(self):
        self.closed = True


def _stream_client(settings, monkeypatch, stream, *, status=200, content_type="text/event-stream"):
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    monkeypatch.setenv("LLM_API_KEY", "provider-secret-for-test")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "~deepseek/deepseek-flash-latest")
    StreamingProvider.instances = []

    class FakeProvider(StreamingProvider):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.response = httpx.Response(
                status,
                headers={"content-type": content_type, "x-provider-secret": "must-not-forward"},
                stream=stream,
            )

    monkeypatch.setattr("xeonolab.services.httpx.AsyncClient", FakeProvider)
    return TestClient(create_app("llm-gateway", settings))


def test_streaming_forwards_sse_deltas_and_tool_calls_without_provider_headers(settings, monkeypatch):
    chunks = [
        b'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":1,"model":"upstream-model","choices":[{"index":0,"delta":{"role":"assistant","tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"xelibrary.search","arguments":""}}]},"finish_reason":null}]}\n\n',
        b'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":1,"model":"upstream-model","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\\"query\\\":\\\"x\\\"}"}}]},"finish_reason":null}]}\n\n',
        b'data: {"id":"cmpl-1","object":"chat.completion.chunk","created":1,"model":"upstream-model","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    stream = ProviderStream(chunks)
    client = _stream_client(settings, monkeypatch, stream)
    response = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "deepseek-flash", "stream": True, "tools": [{"type": "function"}]},
    )

    provider = StreamingProvider.instances[-1]
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.content == b"".join(chunks)
    assert b'"arguments":"{\\\"query\\\":\\\"x\\\"}"' in response.content
    assert response.headers.get("x-provider-secret") is None
    assert provider.sent_request.headers["authorization"] == "Bearer provider-secret-for-test"
    assert provider.sent_request.headers.get("x-provider-secret") is None
    assert provider.sent_request.headers.get("authorization") != auth()["Authorization"]
    assert json.loads(provider.sent_request.content)["model"] == "~deepseek/deepseek-flash-latest"
    assert json.loads(provider.sent_request.content)["stream"] is True
    assert stream.closed is True
    assert provider.closed is True


def test_streaming_rejects_unknown_model_and_missing_llm_scope(settings, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    monkeypatch.setenv("LLM_API_KEY", "provider-secret-for-test")
    client = TestClient(create_app("llm-gateway", settings))
    assert client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "other", "stream": True},
    ).status_code == 422
    assert client.post(
        "/v1/chat/completions",
        headers=auth("bob"),
        json={"model": "deepseek-flash", "stream": True},
    ).status_code == 403
    assert client.post(
        "/v1/chat/completions",
        json={"model": "deepseek-flash", "stream": True},
    ).status_code == 401


def test_streaming_maps_upstream_http_and_invalid_sse_errors(settings, monkeypatch):
    for status, content_type in [(429, "application/json"), (200, "application/json")]:
        stream = ProviderStream([b'{"error":"provider-secret-for-test"}'])
        client = _stream_client(settings, monkeypatch, stream, status=status, content_type=content_type)
        response = client.post(
            "/v1/chat/completions",
            headers=auth(),
            json={"model": "deepseek-flash", "stream": True},
        )
        assert response.status_code == 502
        assert response.json()["error"]["code"] == "PROVIDER_ERROR"
        assert "provider-secret-for-test" not in response.text
        assert "must-not-forward" not in response.headers
        assert stream.closed is True
        assert StreamingProvider.instances[-1].closed is True


def test_streaming_maps_upstream_connection_errors_without_leaking_details(settings, monkeypatch):
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    monkeypatch.setenv("LLM_API_KEY", "provider-secret-for-test")
    StreamingProvider.instances = []

    class FailingProvider(StreamingProvider):
        async def send(self, request, stream=False):
            raise httpx.ReadTimeout("provider-secret-for-test")

    monkeypatch.setattr("xeonolab.services.httpx.AsyncClient", FailingProvider)
    response = TestClient(create_app("llm-gateway", settings)).post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "deepseek-flash", "stream": True},
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "PROVIDER_ERROR"
    assert "provider-secret-for-test" not in response.text
    assert StreamingProvider.instances[-1].closed is True


def test_streaming_preserves_rate_limit_and_audit(settings, monkeypatch, caplog):
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    settings.rate_limit_policies["agent"] = {"requests": 1, "window_seconds": 60}
    caplog.set_level(logging.WARNING, logger="xeonolab.audit")
    stream = ProviderStream([b"data: [DONE]\n\n"])
    client = _stream_client(settings, monkeypatch, stream)

    assert client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "deepseek-flash", "stream": True},
    ).status_code == 200
    limited = client.post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "deepseek-flash", "stream": True},
    )
    assert limited.status_code == 429
    assert limited.headers["Retry-After"] == "60"
    assert len(StreamingProvider.instances) == 1
    events = [json.loads(record.message) for record in caplog.records if "rate_limit.rejected" in record.message]
    assert events[0]["route_group"] == "agent"
    assert events[0]["route"] == "/v1/chat/completions"
    assert "provider-secret-for-test" not in caplog.text


def test_streaming_truncates_and_closes_on_midstream_provider_error(settings, monkeypatch, caplog):
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    monkeypatch.setenv("LLM_API_KEY", "provider-secret-for-test")
    StreamingProvider.instances = []
    caplog.set_level(logging.WARNING, logger="xeonolab")

    class BrokenStream(httpx.AsyncByteStream):
        def __init__(self):
            self.closed = False

        async def __aiter__(self):
            yield b"data: partial\n\n"
            raise httpx.ReadError("provider-secret-for-test")

        async def aclose(self):
            self.closed = True

    broken_stream = BrokenStream()

    class FakeProvider(StreamingProvider):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.response = httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=broken_stream,
            )

    monkeypatch.setattr("xeonolab.services.httpx.AsyncClient", FakeProvider)
    response = TestClient(create_app("llm-gateway", settings)).post(
        "/v1/chat/completions",
        headers=auth(),
        json={"model": "deepseek-flash", "stream": True},
    )
    assert response.status_code == 200
    assert response.content == b"data: partial\n\n"
    assert b"[DONE]" not in response.content
    assert broken_stream.closed is True
    assert StreamingProvider.instances[-1].closed is True
    errors = [json.loads(record.message) for record in caplog.records if "provider_stream.interrupted" in record.message]
    assert errors[0]["code"] == "upstream_read_failed"
    assert errors[0]["request_id"]
    assert "provider-secret-for-test" not in caplog.text


def test_streaming_closes_provider_when_client_disconnects(settings, monkeypatch):
    chunks = ProviderStream([b"data: first\n\n", b"data: second\n\n"])
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    monkeypatch.setenv("LLM_API_KEY", "provider-secret-for-test")
    StreamingProvider.instances = []

    class FakeProvider(StreamingProvider):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.response = httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=chunks,
            )

    monkeypatch.setattr("xeonolab.services.httpx.AsyncClient", FakeProvider)
    app = create_app("llm-gateway", settings)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/v1/chat/completions")

    async def run():
        async def receive():
            return {
                "type": "http.request",
                "body": b'{"model":"deepseek-flash","stream":true}',
                "more_body": False,
            }

        request = Request(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "POST",
                "scheme": "http",
                "path": "/v1/chat/completions",
                "raw_path": b"/v1/chat/completions",
                "query_string": b"",
                "headers": [(b"content-type", b"application/json")],
                "server": ("test", 80),
                "client": ("test", 123),
            },
            receive,
        )
        response = await endpoint(request, SimpleNamespace())
        iterator = response.body_iterator
        assert await anext(iterator) == b"data: first\n\n"
        await iterator.aclose()

    asyncio.run(run())
    assert chunks.closed is True
    assert StreamingProvider.instances[-1].closed is True
