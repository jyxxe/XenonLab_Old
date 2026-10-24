"""Provider availability is shared by read/encode paths without granting scopes."""
import io
import threading
from dataclasses import replace

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import UploadFile
from starlette.requests import Request

from xeonolab.capability_lifecycle import D100M_CAPABILITY, QWEN_CAPABILITY
from xeonolab.common import APIError, Identity
from xeonolab.desktop import create_app as desktop_app
from xeonolab.qwen_encoder import MODEL_ID
from xeonolab.services import create_app

from .conftest import auth
from .test_capability_lifecycle import authority as authority
from .test_capability_lifecycle import patch
from .test_embedding_queue import StubModel


class Fast(StubModel):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def encode(self, *args, **kwargs):
        self.calls += 1
        return super().encode(*args, **kwargs)


class Quality:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = 0

    def start(self):
        pass

    def close(self):
        pass

    def descriptor(self):
        return {'id': MODEL_ID, 'alias': 'quality', 'dimensions': 4, 'max_tokens': 128,
                'normalized': True, 'status': 'ready', 'model_revision': 'synthetic-qwen',
                'preprocessing_revision': 'synthetic-pre'}

    def encode(self, texts, purpose, truncation="reject", expected=None, image=None):
        self.calls += 1
        return [[1.0, 0.0, 0.0, 0.0] for _ in texts], 7


def png():
    output = io.BytesIO()
    Image.new('RGB', (2, 2), 'blue').save(output, format='PNG')
    return output.getvalue()


@pytest.fixture
def embedding_system(authority, monkeypatch, request):
    client, _, settings = authority
    if getattr(request, "param", None) is not None:
        monkeypatch.setenv("EMBEDDING_CAPABILITY_ADAPTERS", request.param)
    settings.rate_limit_enabled = False
    settings.dev_tokens['reader'] = {'sub': 'reader', 'scope': 'embedding:read desktop:read', 'agent_kind': 'human'}
    settings.dev_tokens['writer'] = {'sub': 'writer', 'scope': 'embedding:write dev:read desktop:read', 'agent_kind': 'human'}
    monkeypatch.delenv('D100M_MODEL_PATH', raising=False)
    monkeypatch.setenv('AUTHORIZATION_URL', 'https://authority.example/api/v1/auth/authorization')
    original_get = httpx.Client.get
    mode = {'missing': False, 'unavailable': False}

    def get(instance, url, **kwargs):
        if isinstance(instance, TestClient):
            return original_get(instance, url, **kwargs)
        assert url == 'https://authority.example/api/v1/auth/authorization'
        if mode['unavailable']:
            raise httpx.ConnectError('SYNTHETIC_PRIVATE_FAILURE')
        result = client.get('/api/v1/auth/authorization', headers=kwargs['headers'])
        data = result.json()
        if mode['missing'] and result.status_code == 200:
            data['data'].pop('capability_lifecycle')
        return httpx.Response(result.status_code, json=data, request=httpx.Request('GET', url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    apps, workers = [], []
    for index in range(2):
        local = replace(settings, data_dir=settings.data_dir / f'embedding-{index}')
        app = create_app('embedding', local)
        fast, quality = Fast(), Quality()
        app.state.model, app.state.quality = fast, quality
        apps.append(TestClient(app))
        workers.append((fast, quality))
    return client, apps, workers, mode, settings


@pytest.mark.parametrize('slot,capability,aliases', [
    ('fast', D100M_CAPABILITY, ('fast', 'D100M-Embedding')),
    ('quality', QWEN_CAPABILITY, ('quality', MODEL_ID)),
])
def test_group_stop_is_durable_across_aliases_endpoints_instances_and_scopes(embedding_system, slot, capability, aliases):
    authority_client, clients, workers, _, settings = embedding_system
    path = f'/api/v1/capabilities/{capability}/lifecycle'
    writer = auth('writer')
    assert clients[0].get('/v1/models', headers=writer).status_code == 403
    body = {'model': slot, 'input': ['synthetic']}
    assert clients[0].post('/v1/embeddings', headers=auth('reader'), json=body).status_code == 403
    for client in clients:
        assert client.post('/v1/embeddings', headers=writer, json=body).status_code == 200
    if slot == 'quality':
        assert clients[0].post('/api/v1/embed/image', headers=writer,
                               files={'upload': ('synthetic.png', png(), 'image/png')}).status_code == 200
    child = '/api/v1/capabilities/' + capability + '-encode'
    has_child = authority_client.get(child, headers=writer).status_code == 200
    assert authority_client.patch(path, headers=auth('admin'), json=patch()).status_code == 200
    for client, (fast, quality) in zip(clients, workers):
        if hasattr(client.app.state, 'providers'):
            client.app.state.providers.set_enabled(slot, True)
        before = (fast.calls, quality.calls)
        queue = client.get('/api/v1/embedding/requests', headers=writer).json()['data']
        for endpoint in ('/v1/models', '/api/v1/embed/models'):
            models = client.get(endpoint, headers=auth('reader'))
            assert models.status_code == 200 and models.headers['Cache-Control'] == 'no-store'
            assert slot not in {item['alias'] for item in models.json()['data']}
        for alias in aliases:
            for endpoint in ('/v1/embeddings', '/api/v1/embed/text'):
                assert client.post(endpoint, headers=writer, json={**body, 'model': alias}).status_code == 404
        if slot == 'quality':
            assert client.post('/api/v1/embed/image', headers=writer,
                               files={'upload': ('synthetic.png', png(), 'image/png')}).status_code == 404
        assert (fast.calls, quality.calls) == before
        assert client.get('/api/v1/embedding/requests', headers=writer).json()['data'] == queue
        other = 'quality' if slot == 'fast' else 'fast'
        assert client.post('/api/v1/embed/text', headers=writer, json={**body, 'model': other}).status_code == 200
        assert client.get('/health/ready').status_code == 200  # health != account admission
    assert authority_client.get('/api/v1/capabilities/' + capability, headers=auth('reader')).status_code == 404
    assert authority_client.get(child, headers=writer).status_code == 404
    listed = authority_client.get('/api/v1/capabilities', headers=writer).json()['data']
    assert not any(c['id'] in {capability, capability + '-encode'} for c in listed)
    restarted = TestClient(desktop_app(settings))
    assert not restarted.get(path, headers=auth('admin')).json()['data']['enabled']
    assert restarted.patch(path, headers=auth('admin'), json=patch(True, 1)).status_code == 200
    assert clients[0].post('/v1/embeddings', headers=writer, json=body).status_code == 200
    if has_child:
        assert restarted.get(child, headers=writer).status_code == 200


def test_image_guard_precedes_file_read_and_does_not_require_read_scope(embedding_system):
    _, clients, _, _, _ = embedding_system
    endpoint = next(r.endpoint for r in clients[0].app.routes if getattr(r, 'path', '') == '/api/v1/embed/image')
    actor = Identity('synthetic-writer', {'embedding:write'}, lifecycle_revision=1,
                     disabled_capabilities=frozenset({QWEN_CAPABILITY}))

    class Unreadable(io.BytesIO):
        def read(self, *args):
            pytest.fail('stopped Qwen must not read the parsed upload')

    file = Unreadable(b'synthetic')
    with pytest.raises(APIError) as error:
        endpoint(Request({'type': 'http'}), UploadFile(file, filename='synthetic.png'), model='quality',
                 purpose='document', model_revision=None, dimensions=None, preprocessing_revision=None, actor=actor)
    assert file.closed
    assert error.value.status == 404
    actor.require('embedding:write')
    with pytest.raises(APIError):
        actor.require('embedding:read')
    assert actor.capability_disabled(QWEN_CAPABILITY + '-encode')
    assert not actor.capability_disabled(D100M_CAPABILITY + '-encode')


def test_authority_failure_and_missing_snapshot_cannot_start_encoders(embedding_system):
    _, clients, workers, mode, _ = embedding_system
    for field in ('missing', 'unavailable'):
        mode[field] = True
        for alias in ('fast', 'quality'):
            response = clients[0].post('/v1/embeddings', headers=auth('writer'), json={'model': alias, 'input': 'synthetic'})
            assert response.status_code == 503 and 'PRIVATE' not in response.text
        mode[field] = False
    assert all(fast.calls == quality.calls == 0 for fast, quality in workers)


@pytest.mark.parametrize("embedding_system", ["0", "1"], indirect=True)
def test_replacement_provider_obeys_live_root_and_write_only_scope(embedding_system):
    from .test_embedding_providers import SyntheticProvider, binding

    manager, clients, _, _, _ = embedding_system

    class Replacement(SyntheticProvider):
        def __init__(self):
            super().__init__()
            self.prepared = self.encoded = 0

        def prepare(self):
            self.prepared += 1
            return self

        def encode(self, *args, **kwargs):
            self.encoded += 1
            return super().encode(*args, **kwargs)

    provider = Replacement()
    clients[0].app.state.providers.replace(binding(provider))
    with clients[0] as client:
        body = {"model": "fast", "input": ["alpha"]}
        result = client.post("/v1/embeddings", headers=auth("writer"), json=body)
        assert result.status_code == 200 and result.json()["model"] == "synthetic-fast"
        history = client.get("/api/v1/embedding/requests", headers=auth("writer")).json()["data"]
        assert history[0]["model"] == "syntheticfast"
        assert client.get("/v1/models", headers=auth("writer")).status_code == 403
        path = f"/api/v1/capabilities/{D100M_CAPABILITY}/lifecycle"
        assert manager.patch(path, headers=auth("admin"), json=patch()).status_code == 200
        client.app.state.providers.set_enabled("fast", True)
        for endpoint in ("/v1/embeddings", "/api/v1/embed/text"):
            result = client.post(endpoint, headers=auth("writer"), json=body)
            assert result.status_code == 404
        assert (provider.prepared, provider.encoded) == (1, 1)
        assert client.get("/api/v1/embedding/requests", headers=auth("writer")).json()["data"] == history
        models = client.get("/v1/models", headers=auth("reader"))
        assert models.status_code == 200 and "synthetic-fast" not in {item["id"] for item in models.json()["data"]}
        assert manager.patch(path, headers=auth("admin"), json=patch(True, 1)).status_code == 200
        assert client.post("/api/v1/embed/text", headers=auth("writer"), json=body).status_code == 200
        assert (provider.prepared, provider.encoded) == (2, 2)
    assert provider.started == provider.closed == 1
