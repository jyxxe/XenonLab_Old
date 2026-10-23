"""Actual LLM discovery/JSON/SSE boundaries obey the live core authority."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from xeonolab.capability_lifecycle import LLM_CAPABILITY
from xeonolab.desktop import create_app as desktop_app
from xeonolab.services import create_app

from .conftest import auth
from .test_capability_lifecycle import authority as authority
from .test_capability_lifecycle import patch

PATH = f'/api/v1/capabilities/{LLM_CAPABILITY}/lifecycle'
BODY = {'model': 'deepseek-flash', 'messages': [{'role': 'user', 'content': 'synthetic'}]}


@pytest.fixture
def llm_system(authority, monkeypatch):
    client, _, settings = authority
    settings.rate_limit_enabled = False
    for token in ('alice', 'bob'):
        settings.dev_tokens[token]['scope'] += ' llm:use'
    monkeypatch.setenv('AUTHORIZATION_URL', 'https://authority.example/api/v1/auth/authorization')
    monkeypatch.setenv('LLM_API_KEY', 'SYNTHETIC_PROVIDER_KEY')
    monkeypatch.delenv('LLM_API_KEY_FILE', raising=False)
    monkeypatch.setenv('LLM_BASE_URL', 'https://upstream.example')
    original_get = httpx.Client.get
    original_async = httpx.AsyncClient
    mode = {'missing': False, 'unavailable': False, 'disable_on_stream': False}
    calls, streams = [], []

    def get(instance, url, **kwargs):
        if isinstance(instance, TestClient):
            return original_get(instance, url, **kwargs)
        assert url == 'https://authority.example/api/v1/auth/authorization'
        if mode['unavailable']:
            raise httpx.ConnectError('SYNTHETIC_PRIVATE_UPSTREAM')
        result = client.get('/api/v1/auth/authorization', headers=kwargs['headers'])
        data = result.json()
        if mode['missing'] and result.status_code == 200:
            data['data'].pop('capability_lifecycle')
        return httpx.Response(result.status_code, json=data, request=httpx.Request('GET', url))

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield b'data: {"choices":[{"index":0,"delta":{"content":"synthetic"},"finish_reason":null}]}\n\n'
            yield b'data: [DONE]\n\n'

        async def aclose(self):
            self.closed = True

    def upstream(request):
        assert request.url.host == 'upstream.example'
        assert request.headers['Authorization'] == 'Bearer SYNTHETIC_PROVIDER_KEY'
        body = json.loads(request.content)
        calls.append(body)
        if body.get('stream'):
            if mode['disable_on_stream']:
                assert client.patch(PATH, headers=auth('admin'), json=patch()).status_code == 200
            stream = Stream()
            streams.append(stream)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'}, stream=stream)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'synthetic'}}]})

    monkeypatch.setattr(httpx.Client, 'get', get)
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original_async(**kw, transport=httpx.MockTransport(upstream)))
    apps = [create_app('llm-gateway', settings) for _ in range(2)]
    return client, [TestClient(app) for app in apps], calls, streams, mode, settings


def test_llm_disable_restart_reenable_and_query_control_are_independent(llm_system):
    authority_client, clients, calls, streams, _, settings = llm_system
    assert clients[0].get('/v1/models', headers=auth()).json()['data'][0]['id'] == 'deepseek-flash'
    for stream in (False, True):
        result = clients[0].post('/v1/chat/completions', headers=auth(), json={**BODY, 'stream': stream})
        assert result.status_code == 200
    assert len(calls) == 2 and streams[0].closed
    assert clients[0].app.state.llm_stream_slots.qsize() == 8
    stopped = authority_client.patch(PATH, headers=auth('admin'), json=patch())
    assert stopped.status_code == 200, stopped.text
    for token in ('alice', 'bob'):
        assert authority_client.get('/api/v1/capabilities/' + LLM_CAPABILITY, headers=auth(token)).status_code == 404
        listed = authority_client.get('/api/v1/capabilities', headers=auth(token)).json()['data']
        assert LLM_CAPABILITY not in {c['id'] for c in listed}
        for client in clients:
            # A local enable in the adapter implementation cannot override core denial.
            if hasattr(client.app.state, 'chat_providers'):
                client.app.state.chat_providers.set_enabled(True)
            result = client.get('/v1/models', headers=auth(token))
            assert result.status_code == 404 and result.headers['Cache-Control'] == 'no-store'
            for stream in (False, True):
                assert client.post('/v1/chat/completions', headers=auth(token), json={**BODY, 'stream': stream}).status_code == 404
            assert client.app.state.llm_stream_slots.qsize() == 8
            assert client.get('/health/ready').status_code == 200
    assert len(calls) == 2
    restarted = TestClient(desktop_app(settings))
    assert restarted.get(PATH, headers=auth('admin')).json()['data']['enabled'] is False
    # Shared revision, separate capability state: query remains enabled.
    assert restarted.get('/api/v1/capabilities/builtin.platform.tool.xelibrary-query', headers=auth()).status_code == 200
    assert restarted.patch(PATH, headers=auth('admin'), json=patch(True, 1)).status_code == 200
    assert clients[1].get('/v1/models', headers=auth()).status_code == 200
    assert clients[1].post('/v1/chat/completions', headers=auth(), json=BODY).status_code == 200
    assert len(calls) == 3


def test_llm_denies_before_reading_invalid_body_and_no_slots_are_consumed(llm_system):
    authority_client, clients, calls, _, _, _ = llm_system
    assert authority_client.patch(PATH, headers=auth('admin'), json=patch()).status_code == 200
    for client in clients:
        response = client.post('/v1/chat/completions', headers=auth(), content=b'not JSON')
        assert response.status_code == 404
        assert client.app.state.llm_stream_slots.qsize() == 8
    assert not calls


def test_missing_authority_snapshot_outage_and_revoked_scope_fail_closed(llm_system):
    _, clients, calls, _, mode, settings = llm_system
    mode['missing'] = True
    assert clients[0].get('/v1/models', headers=auth()).status_code == 503
    assert clients[0].post('/v1/chat/completions', headers=auth(), json=BODY).status_code == 503
    mode['missing'] = False
    mode['unavailable'] = True
    result = clients[0].post('/v1/chat/completions', headers=auth(), json=BODY)
    assert result.status_code == 503 and 'PRIVATE' not in result.text
    mode['unavailable'] = False
    settings.dev_tokens['alice']['scope'] = 'desktop:read'
    assert clients[0].get('/v1/models', headers=auth()).status_code == 403
    assert clients[0].post('/v1/chat/completions', headers=auth(), json=BODY).status_code == 403
    assert not calls


def test_already_admitted_stream_drains_but_next_request_is_denied(llm_system):
    _, clients, calls, streams, mode, _ = llm_system
    mode['disable_on_stream'] = True
    streamed = clients[0].post('/v1/chat/completions', headers=auth(), json={**BODY, 'stream': True})
    assert streamed.status_code == 200 and streamed.content.endswith(b'data: [DONE]\n\n')
    assert streams[0].closed and clients[0].app.state.llm_stream_slots.qsize() == 8
    assert clients[1].post('/v1/chat/completions', headers=auth(), json=BODY).status_code == 404
    assert len(calls) == 1
