import asyncio
import copy
import json
import ssl
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from xeonolab.capabilities import CapabilityRegistry, load_registry
from xeonolab.chat_provider import ChatProviderRegistry, HTTPChatCall, HTTPChatProvider
from xeonolab.common import APIError, Identity
from xeonolab.services import create_app

from .conftest import auth
from .test_llm_gateway import _stream_client


class SyntheticProvider:
    def __init__(self, call):
        self.call = call

    def configured(self):
        return True

    def models(self):
        return [{"id": "deepseek-flash", "object": "model"}]

    def prepare(self, body):
        return self.call


def gateway(settings):
    settings.rate_limit_enabled = False
    settings.dev_tokens["alice"]["scope"] += " llm:use"
    app = create_app("llm-gateway", settings)
    return app, TestClient(app)


def manifest_with(**overrides):
    manifest = copy.deepcopy(load_registry().all())
    for capability in manifest["capabilities"]:
        if capability["id"] == "builtin.platform.provider.llm-chat":
            capability.update(overrides)
    return CapabilityRegistry(manifest)


def test_default_provider_snapshots_config_keeps_tls_and_hides_credential_repr(tmp_path, monkeypatch):
    keyfile = tmp_path / "synthetic-provider-key"
    keyfile.write_text("SYNTHETIC_FILE_KEY")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(keyfile))
    monkeypatch.setenv("LLM_API_KEY", "SYNTHETIC_ENV_KEY")
    monkeypatch.setenv("LLM_BASE_URL", "https://provider.example/")
    monkeypatch.setenv("LLM_PROVIDER_MODEL", "private-backend-model")
    monkeypatch.setenv("LLM_TLS_MAX_VERSION", "1.2")
    provider = HTTPChatProvider()
    body = {"model": "deepseek-flash", "messages": []}
    call = provider.prepare(body)
    keyfile.write_text("SYNTHETIC_ROTATED_KEY")
    assert provider.configured()
    assert call.headers == {"Authorization": "Bearer SYNTHETIC_FILE_KEY"}
    assert provider.prepare(body).headers == {"Authorization": "Bearer SYNTHETIC_ROTATED_KEY"}
    assert call.url == "https://provider.example/v1/chat/completions"
    assert call.body["model"] == "private-backend-model" and body["model"] == "deepseek-flash"
    assert call.context.verify_mode == ssl.CERT_REQUIRED and call.context.check_hostname
    assert call.context.maximum_version == ssl.TLSVersion.TLSv1_2
    assert "SYNTHETIC" not in repr(call)
    assert "private-backend-model" not in repr(call)
    assert "private-backend-model" not in json.dumps(provider.models())


def test_replacing_trusted_provider_keeps_public_callers_and_stream_cleanup(settings):
    app, client = gateway(settings)
    requests = []
    streams = []

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'data: {"choices":[{"index":0,"delta":{"content":"alternate"},"finish_reason":null}]}\n\n'
            yield b'data: [DONE]\n\n'

        async def aclose(self):
            self.closed = True

    def upstream(request):
        requests.append(request)
        if json.loads(request.content).get("stream"):
            stream = Stream()
            streams.append(stream)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        return httpx.Response(200, json={"choices": [{"message": {"content": "alternate"}}]})

    class AlternateProvider(SyntheticProvider):
        def prepare(self, body):
            assert body["model"] == "deepseek-flash"
            return HTTPChatCall("https://alternate.example/chat", {**body, "model": "alternate-backend"},
                                {"Authorization": "Bearer SYNTHETIC_ALTERNATE_KEY"}, ssl.create_default_context(),
                                lambda **kwargs: httpx.AsyncClient(**kwargs, transport=httpx.MockTransport(upstream)))

    app.state.chat_providers.replace(AlternateProvider(None))
    assert client.get("/v1/models", headers=auth()).json()["data"][0]["id"] == "deepseek-flash"
    body = {"model": "deepseek-flash", "messages": [{"role": "user", "content": "same caller"}]}
    assert client.post("/v1/chat/completions", headers=auth(), json=body).json()["choices"][0]["message"]["content"] == "alternate"
    streamed = client.post("/v1/chat/completions", headers=auth(), json={**body, "stream": True})
    assert b"alternate" in streamed.content and streamed.content.endswith(b"data: [DONE]\n\n")
    assert all(request.headers["Authorization"] == "Bearer SYNTHETIC_ALTERNATE_KEY" for request in requests)
    assert all(request.headers["Authorization"] != "Bearer alice" for request in requests)
    assert all(json.loads(request.content)["model"] == "alternate-backend" for request in requests)
    assert streams[0].closed and app.state.llm_stream_slots.qsize() == 8
    app.state.chat_providers.set_enabled(False)
    assert client.get("/v1/models", headers=auth()).status_code == 404
    assert client.post("/v1/chat/completions", headers=auth(), json=body).status_code == 404
    assert client.get("/health/ready").status_code == 503
    assert len(requests) == 2
    app.state.chat_providers.set_enabled(True)
    assert client.get("/health/ready").status_code == 200


@pytest.mark.parametrize("overrides,code", [
    ({"kind": "tool"}, "CAPABILITY_INVALID"), ({"runtime": "ui"}, "CAPABILITY_INVALID"),
    ({"permissions": []}, "CAPABILITY_INVALID"), ({"version": "2.0.0"}, "CAPABILITY_INCOMPATIBLE"),
    ({"dependencies": ["builtin.platform.app.knowledge@1.0.0"]}, "CAPABILITY_DEPENDENCY_UNAVAILABLE"),
])
def test_provider_registry_enforces_capability_contract(overrides, code):
    registry = ChatProviderRegistry(SyntheticProvider(None), registry_loader=lambda: manifest_with(**overrides))
    with pytest.raises(APIError) as error:
        registry.resolve(Identity("synthetic", {"llm:use"}))
    assert error.value.code == code
    assert registry.ready() is False


def test_provider_registry_rejects_missing_capability_and_scopes(settings):
    provider = SyntheticProvider(None)
    registry = ChatProviderRegistry(provider)
    with pytest.raises(APIError) as error:
        registry.resolve(Identity("synthetic", set()))
    assert error.value.status == 403
    missing = ChatProviderRegistry(provider, capability_id="plugin.unregistered.provider")
    with pytest.raises(APIError) as error:
        missing.resolve(Identity("synthetic", {"llm:use"}))
    assert error.value.status == 404
    app, client = gateway(settings)
    app.state.chat_providers.replace(provider)
    assert client.post("/v1/chat/completions", headers=auth("bob"), json={"model": "deepseek-flash"}).status_code == 403
    assert app.state.llm_stream_slots.qsize() == 8


@pytest.mark.parametrize("budget", [None, 1])
def test_json_deadline_cancels_provider_and_gateway_recovers(settings, budget):
    cancelled = []

    class HangingCall:
        async def complete(self):
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.append(True)

    class GoodCall:
        async def complete(self):
            return {"choices": []}

    app, client = gateway(settings)
    app.state.llm_stream_timeout_seconds = .02 if budget is None else 10
    provider = SyntheticProvider(HangingCall())
    app.state.chat_providers = ChatProviderRegistry(provider, registry_loader=(
        load_registry if budget is None else lambda: manifest_with(timeout_seconds=budget)
    ))
    start = time.monotonic()
    response = client.post("/v1/chat/completions", headers=auth(), json={"model": "deepseek-flash"})
    assert response.status_code == 504 and response.json()["error"]["code"] == "PROVIDER_TIMEOUT"
    assert time.monotonic() - start < 2 and cancelled == [True]
    assert app.state.llm_stream_slots.qsize() == 8
    app.state.chat_providers.replace(SyntheticProvider(GoodCall()))
    assert client.post("/v1/chat/completions", headers=auth(), json={"model": "deepseek-flash"}).status_code == 200


@pytest.mark.parametrize("stage", ["models", "prepare", "complete", "serialization", "stream_client"])
def test_adapter_faults_are_safe_and_release_capacity(settings, stage):
    class BadCall:
        async def complete(self):
            if stage == "serialization":
                return {"bad": {1, 2}}
            raise APIError(500, "PRIVATE", "SYNTHETIC_PRIVATE_DETAIL")

        def stream_client(self):
            raise APIError(500, "PRIVATE", "SYNTHETIC_PRIVATE_DETAIL")

    class BadProvider(SyntheticProvider):
        def models(self):
            raise APIError(500, "PRIVATE", "SYNTHETIC_PRIVATE_DETAIL")

        def prepare(self, body):
            if stage == "prepare":
                raise APIError(422, "UNSUPPORTED_REQUEST", "SYNTHETIC_PRIVATE_DETAIL")
            return self.call

    app, client = gateway(settings)
    app.state.chat_providers.replace(BadProvider(BadCall()))
    response = client.get("/v1/models", headers=auth()) if stage == "models" else client.post(
        "/v1/chat/completions", headers=auth(), json={"model": "deepseek-flash", "stream": stage == "stream_client"})
    assert response.status_code == (422 if stage == "prepare" else 502)
    assert "SYNTHETIC_PRIVATE_DETAIL" not in response.text
    assert app.state.llm_stream_slots.qsize() == 8


def test_sse_deadline_and_unexpected_adapter_failures_close_resources(settings, monkeypatch):
    for failure in ("timeout", "unexpected"):
        class Stream(httpx.AsyncByteStream):
            closed = False

            async def __aiter__(self):
                yield b'data: {"choices":[{"index":0,"delta":{},"finish_reason":null}]}\n\n'
                if failure == "timeout":
                    await asyncio.Event().wait()
                raise RuntimeError("SYNTHETIC_PRIVATE_DETAIL")

            async def aclose(self):
                self.closed = True

        stream = Stream()
        client = _stream_client(settings, monkeypatch, stream)
        client.app.state.llm_stream_timeout_seconds = .1
        response = client.post("/v1/chat/completions", headers=auth(), json={"model": "deepseek-flash", "stream": True})
        assert response.status_code == 200 and b'"code":"PROVIDER_ERROR"' in response.content
        assert b"[DONE]" not in response.content and b"SYNTHETIC_PRIVATE_DETAIL" not in response.content
        assert stream.closed and client.app.state.llm_stream_slots.qsize() == 8


def test_provider_flag_is_validated_and_keeps_default_rollback_binding(settings, monkeypatch):
    monkeypatch.setenv("LLM_CAPABILITY_ADAPTERS", "0")
    monkeypatch.delenv("LLM_API_KEY_FILE", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    app, client = gateway(settings)
    app.state.chat_providers.set_enabled(False)
    assert client.get("/v1/models", headers=auth()).status_code == 200
    assert client.post("/v1/chat/completions", headers=auth(), json={"model": "deepseek-flash"}).status_code == 503
    assert app.state.llm_capability_adapters is False
    monkeypatch.setenv("LLM_CAPABILITY_ADAPTERS", "bad")
    with pytest.raises(ValueError, match="LLM_CAPABILITY_ADAPTERS"):
        create_app("llm-gateway", settings)


def test_registry_defensively_rejects_a_corrupted_in_memory_budget():
    manifest = manifest_with()
    manifest.lookup("builtin.platform.provider.llm-chat")["timeout_seconds"] = True
    registry = ChatProviderRegistry(SyntheticProvider(None), registry_loader=lambda: manifest)
    with pytest.raises(APIError) as error:
        registry.resolve(Identity("synthetic", {"llm:use"}))
    assert error.value.code == "CAPABILITY_INVALID" and not registry.ready()


class LifecycleActor(Identity):
    def __init__(self, disabled=False):
        super().__init__('synthetic', {'llm:use'})
        self.lifecycle_revision = 3
        self.disabled = disabled

    def require_capability(self, capability_id):
        assert capability_id == 'builtin.platform.provider.llm-chat'
        if self.disabled:
            raise APIError(404, 'CAPABILITY_NOT_FOUND', 'no such capability')


def test_authority_denial_cannot_be_overridden_by_local_enable():
    registry = ChatProviderRegistry(SyntheticProvider(None), lifecycle_required=True)
    denied = LifecycleActor(True)
    registry.set_enabled(True)
    with pytest.raises(APIError) as error:
        registry.resolve(denied)
    assert error.value.status == 404
    assert registry.resolve(LifecycleActor()).provider.configured()
    registry.set_enabled(False)
    with pytest.raises(APIError):
        registry.resolve(LifecycleActor())


def test_strict_chat_registry_requires_a_verified_core_snapshot():
    registry = ChatProviderRegistry(SyntheticProvider(None), lifecycle_required=True)
    unverified = LifecycleActor()
    unverified.lifecycle_revision = None
    for identity in (Identity('old-core', {'llm:use'}), unverified):
        with pytest.raises(APIError) as error:
            registry.resolve(identity)
        assert error.value.code == 'CAPABILITY_LIFECYCLE_UNAVAILABLE'
    # Actorless process readiness is not an account authorization decision.
    assert registry.ready()


@pytest.mark.parametrize('flag', ['0', '1'])
def test_authority_gate_precedes_body_capacity_and_both_provider_paths(settings, monkeypatch, flag):
    from starlette.requests import Request

    monkeypatch.setenv('LLM_CAPABILITY_ADAPTERS', flag)
    app, client = gateway(settings)
    denied = LifecycleActor(True)
    app.state.authorization_resolver = lambda token: denied
    app.state.chat_providers.set_enabled(True)
    assert client.get('/v1/models', headers=auth()).status_code == 404
    for stream in (False, True):
        assert client.post('/v1/chat/completions', headers=auth(), json={'stream': stream}).status_code == 404
    endpoint = next(r.endpoint for r in app.routes if getattr(r, 'path', '') == '/v1/chat/completions')
    before = app.state.llm_stream_slots.qsize()

    async def run():
        async def receive():
            pytest.fail('stopped capability must not read a request body')
        request = Request({'type': 'http', 'method': 'POST', 'path': '/v1/chat/completions', 'headers': []}, receive)
        with pytest.raises(APIError) as error:
            await endpoint(request, denied)
        assert error.value.status == 404
    asyncio.run(run())
    assert app.state.llm_stream_slots.qsize() == before
