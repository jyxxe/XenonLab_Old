"""Index admission and cooperative continuation use the live core authority."""
import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from xeonolab import common, search
from xeonolab.capability_checkpoint import INDEX_CAPABILITY, CapabilityCheckpoint
from xeonolab.common import APIError, Identity

from .conftest import auth
from .test_capability_lifecycle import authority as authority
from .test_capability_lifecycle import patch
from .test_index_status import create_table, make_client

PATH = f'/api/v1/capabilities/{INDEX_CAPABILITY}/lifecycle'


def library(authority, tmp_path, monkeypatch):
    _, authority_app, settings = authority
    client = make_client(settings, tmp_path, monkeypatch)
    client.app.state.authorization_resolver = lambda token: common.token_identity(token, settings)
    client.app.state.capability_lifecycle = authority_app.state.capability_lifecycle
    table = create_table(client)
    result = client.post(f'/api/v1/tables/{table}/rows', headers=auth(), json={'values': {'body': 'alpha beta'}})
    assert result.status_code == 201
    clock = [0.0]
    original = search.index_checkpoint
    def checkpoint(request, actor):
        result = original(request, actor)
        result._clock = lambda: clock[0]
        return result
    monkeypatch.setattr(search, 'index_checkpoint', checkpoint)
    return client, table, clock


def job(client, result):
    assert result.status_code == 202, result.text
    return client.get('/api/v1/jobs/' + result.json()['data']['job_id'], headers=auth('admin')).json()['data']


@pytest.mark.parametrize("strict", ["0", "1"])
def test_stop_blocks_new_work_but_preserves_sources_jobs_and_committed_vectors(authority, tmp_path, monkeypatch, strict):
    manager, _, _ = authority
    client, table, _ = library(authority, tmp_path, monkeypatch)
    payload = {'table_id': table}
    assert job(client, client.post('/api/v1/index/reindex', headers=auth('admin'), json=payload))['status'] == 'completed'
    before = client.get(f'/api/v1/tables/{table}/index-status', headers=auth()).json()['data']
    assert before['chunk_count'] > 0
    assert manager.patch(PATH, headers=auth('admin'), json=patch()).status_code == 200
    monkeypatch.setenv('XELIBRARY_CHUNKER_CAPABILITIES', '0')
    monkeypatch.setenv('CAPABILITY_LIFECYCLE_ENABLED', strict)
    denied = client.post('/api/v1/index/reindex', headers=auth('admin'), json=payload)
    assert denied.status_code == 404
    assert client.get('/api/v1/jobs', headers=auth('admin')).status_code == 200
    after = client.get(f'/api/v1/tables/{table}/index-status', headers=auth()).json()['data']
    assert after['chunk_count'] == before['chunk_count'] and after['state'] == 'ready'
    found = client.post('/api/v1/search/semantic', headers=auth(), json={'query':'alpha', 'path':'/**'})
    assert found.status_code == 200 and len(found.json()['data']) == 1
    assert client.get(f'/api/v1/tables/{table}/rows', headers=auth()).status_code == 200
    assert client.post(f'/api/v1/tables/{table}/rows', headers=auth(), json={'values': {'body': 'new'}}).status_code == 201
    assert manager.patch(PATH, headers=auth('admin'), json=patch(True, 1)).status_code == 200
    assert job(client, client.post('/api/v1/index/reindex', headers=auth('admin'), json=payload))['status'] == 'completed'


def test_queued_job_refreshes_before_first_chunk(authority, tmp_path, monkeypatch):
    manager, _, _ = authority
    client, table, _ = library(authority, tmp_path, monkeypatch)
    queued = []
    monkeypatch.setattr(BackgroundTasks, 'add_task', lambda self, fn, *args, **kwargs: queued.append((fn,args,kwargs)))
    result = client.post('/api/v1/index/reindex', headers=auth('admin'), json={'table_id': table})
    assert job(client, result)['status'] == 'queued'
    assert manager.patch(PATH, headers=auth('admin'), json=patch()).status_code == 200
    for fn, args, kwargs in queued:
        fn(*args, **kwargs)
    assert job(client, result)['status'] == 'failed'
    assert client.get(f'/api/v1/tables/{table}/index-status', headers=auth()).json()['data']['chunk_count'] == 0


@pytest.mark.parametrize('failure', ['stop', 'revoked', 'permission', 'missing', 'outage'])
def test_running_job_refreshes_before_publication(authority, tmp_path, monkeypatch, failure):
    manager, _, settings = authority
    client, table, clock = library(authority, tmp_path, monkeypatch)
    original = search.httpx.Client.post
    calls = []
    def embedding(instance, *args, **kwargs):
        calls.append(True)
        clock[0] += 6
        if failure == 'stop':
            assert manager.patch(PATH, headers=auth('admin'), json=patch()).status_code == 200
        elif failure == 'permission':
            settings.dev_tokens['admin']['scope'] = 'xelibrary:read'
        elif failure == 'missing':
            client.app.state.capability_lifecycle = None
        else:
            def unavailable(token):
                raise APIError(401 if failure == 'revoked' else 503, 'UNAVAILABLE', 'synthetic private detail')
            client.app.state.authorization_resolver = unavailable
        return original(instance, *args, **kwargs)
    monkeypatch.setattr(search.httpx.Client, 'post', embedding)
    result = client.post('/api/v1/index/reindex', headers=auth('admin'), json={'table_id': table})
    # Restore only the test reader, after the worker has already failed.
    client.app.state.authorization_resolver = lambda token: common.token_identity(token, settings)
    client.app.state.capability_lifecycle = authority[1].state.capability_lifecycle
    settings.dev_tokens['admin']['scope'] += ' xelibrary:admin'
    failed = job(client, result)
    assert calls and failed['status'] == 'failed'
    assert 'synthetic private' not in str(failed)
    assert client.get(f'/api/v1/tables/{table}/index-status', headers=auth()).json()['data']['chunk_count'] == 0


def test_checkpoint_rechecks_identity_and_permissions_without_recharging_inbound_limit(monkeypatch):
    from types import SimpleNamespace
    actor = Identity('caller', {'xelibrary:admin'}, lifecycle_revision=0)
    app = SimpleNamespace(state=SimpleNamespace())
    request = Request({'type':'http', 'app':app, 'headers':[(b'authorization',b'Bearer synthetic-credential'), (b'cookie',b'not-copied')]})
    now, calls = [0.0], []
    def refresh(context):
        assert list(context.headers) == ['authorization']
        assert context.app is app
        calls.append(True)
        return actor
    monkeypatch.setattr(common, 'refresh_identity', refresh)
    checkpoint = CapabilityCheckpoint(request, actor, capabilities=(INDEX_CAPABILITY,), permissions=('xelibrary:admin',), clock=lambda:now[0])
    assert 'synthetic-credential' not in repr(checkpoint)
    checkpoint.start()
    for _ in range(100):
        checkpoint()
    assert len(calls) == 1
    now[0] = 5
    checkpoint()
    assert len(calls) == 2
    actor.sub = 'other'
    with pytest.raises(APIError) as error:
        checkpoint()
    assert error.value.code == 'JOB_IDENTITY_CHANGED'


def test_internal_refresh_preserves_authentication_but_not_inbound_rate_charge(settings, monkeypatch):
    from types import SimpleNamespace
    app = SimpleNamespace(state=SimpleNamespace(settings=settings, capability_lifecycle_required=False))
    request = Request({'type':'http', 'app':app, 'headers':[(b'authorization',b'Bearer admin')]})
    charged = []
    monkeypatch.setattr(common, 'enforce_rate_limit', lambda *args: charged.append(True))
    assert common.identity(request).sub == 'admin'
    assert common.refresh_identity(request).sub == 'admin'
    assert charged == [True]
    request = Request({'type':'http','app':app,'headers':[]})
    with pytest.raises(APIError) as error:
        common.refresh_identity(request)
    assert error.value.status == 401


def test_checkpoint_never_serializes_credential(authority, tmp_path, monkeypatch):
    client, table, _ = library(authority, tmp_path, monkeypatch)
    assert job(client, client.post('/api/v1/index/reindex', headers=auth('admin'), json={'table_id':table}))['status'] == 'completed'
    for path in tmp_path.rglob('*.sqlite*'):
        if path.is_file():
            assert b'Bearer admin' not in path.read_bytes()
    # Inspect persisted jobs using the actual API too.
    assert 'Bearer admin' not in str(client.get('/api/v1/jobs', headers=auth('admin')).json())


def test_strict_job_admission_without_authority_fails_before_dependencies(settings, monkeypatch):
    from fastapi.testclient import TestClient

    from xeonolab.services import create_app
    monkeypatch.setenv('CAPABILITY_LIFECYCLE_ENABLED', '1')
    monkeypatch.delenv('AUTHORIZATION_URL', raising=False)
    client = TestClient(create_app('xelibrary', settings))
    result = client.post('/api/v1/index/reindex', headers=auth('admin'), json={'table_id':'unknown'})
    assert result.status_code == 503


def test_remote_authority_checkpoint_reads_new_state_and_fails_closed(authority, monkeypatch):
    from .test_capability_lifecycle import gateway
    manager, _, _ = authority
    consumer, _, mode = gateway(authority, monkeypatch)
    request = Request({'type':'http', 'app':consumer.app, 'method':'POST', 'path':'/api/v1/index/reindex',
                       'headers':[(b'authorization',b'Bearer admin')]})
    actor = common.identity(request)
    now = [0.0]
    checkpoint = CapabilityCheckpoint(request, actor, capabilities=(INDEX_CAPABILITY,),
                                      permissions=('xelibrary:admin',), clock=lambda:now[0])
    checkpoint.start()
    assert manager.patch(PATH, headers=auth('admin'), json=patch()).status_code == 200
    now[0] = 5
    with pytest.raises(APIError) as error:
        checkpoint()
    assert error.value.status == 404
    mode['error'] = 'offline'
    with pytest.raises(APIError) as error:
        checkpoint()
    assert error.value.status == 503


def test_parent_mapping_hides_chunker_without_controlling_child_directly():
    from xeonolab.capability_lifecycle import CONTROLLABLE, capability_disabled
    child = 'builtin.platform.worker.document-chunker'
    assert INDEX_CAPABILITY in CONTROLLABLE and child not in CONTROLLABLE
    assert capability_disabled(child, {INDEX_CAPABILITY})
    assert not capability_disabled('builtin.platform.app.knowledge', {INDEX_CAPABILITY})


def test_index_availability_does_not_require_write_scope_from_admin(authority, tmp_path, monkeypatch):
    client, table, _ = library(authority, tmp_path, monkeypatch)
    authority[2].dev_tokens['admin']['scope'] = 'xelibrary:admin'
    result = client.post('/api/v1/index/reindex', headers=auth('admin'), json={'table_id':table})
    assert job(client, result)['status'] == 'completed'


@pytest.mark.parametrize("adapter_mode", ["0", "1"])
@pytest.mark.parametrize("stop_at", ["queued", "yield"])
def test_replacement_chunker_is_fenced_before_processor_or_embedding(
    authority, tmp_path, monkeypatch, adapter_mode, stop_at,
):
    from fastapi.testclient import TestClient

    from xeonolab.document_chunker import ChunkerBinding
    from xeonolab.services import create_app

    monkeypatch.setenv("XELIBRARY_CHUNKER_CAPABILITIES", adapter_mode)
    manager, _, settings = authority
    original, table, clock = library(authority, tmp_path, monkeypatch)
    client = TestClient(create_app("xelibrary", settings))
    client.app.state.authorization_resolver = original.app.state.authorization_resolver
    client.app.state.capability_lifecycle = original.app.state.capability_lifecycle
    processed, embeddings, queued = [], [], []
    original_post = search.httpx.Client.post

    def embedding(instance, *args, **kwargs):
        embeddings.append(True)
        return original_post(instance, *args, **kwargs)

    monkeypatch.setattr(search.httpx.Client, "post", embedding)

    def replacement(tokenizer, source, path, name):
        processed.append(dict(source))
        if stop_at == "yield":
            assert manager.patch(PATH, headers=auth("admin"), json=patch()).status_code == 200
            clock[0] += 6
        yield "body", source["body"]

    client.app.state.document_chunkers.replace(ChunkerBinding("lifecycle-replacement-v1", replacement))
    if stop_at == "queued":
        monkeypatch.setattr(BackgroundTasks, "add_task",
                            lambda self, fn, *args, **kwargs: queued.append((fn, args, kwargs)))
    result = client.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": table})
    assert result.status_code == 202
    if stop_at == "queued":
        assert manager.patch(PATH, headers=auth("admin"), json=patch()).status_code == 200
        for fn, args, kwargs in queued:
            fn(*args, **kwargs)
        assert not processed
    else:
        assert processed == [{"body": "alpha beta"}]
    assert job(client, result)["status"] == "failed"
    assert not embeddings, "expired continuation must fail before downstream encoding"
    state = client.get(f"/api/v1/tables/{table}/index-status", headers=auth()).json()["data"]
    assert state["chunk_count"] == 0
