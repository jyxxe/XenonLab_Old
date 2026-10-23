"""Live authority lifecycle state controls actual discovery and query admission."""
import concurrent.futures
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from xeonolab.capability_lifecycle import (
    QUERY_CAPABILITY,
    CapabilityLifecycle,
    LifecyclePatch,
    parse_snapshot,
)
from xeonolab.common import APIError, connect
from xeonolab.desktop import create_app as desktop_app
from xeonolab.services import create_app

from .conftest import auth

PATH = f'/api/v1/capabilities/{QUERY_CAPABILITY}/lifecycle'


def patch(enabled=False, generation=0, operation=None):
    return {'enabled': enabled, 'expected_generation': generation, 'operation_id': operation or str(uuid4())}


@pytest.fixture
def authority(settings, monkeypatch):
    monkeypatch.setenv('CAPABILITY_LIFECYCLE_ENABLED', '1')
    for token in ('alice', 'bob', 'admin'):
        settings.dev_tokens[token]['scope'] += ' desktop:read'
    settings.dev_tokens['admin']['scope'] += ' capabilities:manage desktop:admin'
    app = desktop_app(settings)
    return TestClient(app), app, settings


def gateway(authority, monkeypatch):
    client, _, settings = authority
    monkeypatch.setenv('AUTHORIZATION_URL', 'https://authority.example/api/v1/auth/authorization')
    original = httpx.Client.get
    calls = []
    mode = {'error': None, 'snapshot': 'actual'}

    def get(instance, url, **kwargs):
        if isinstance(instance, TestClient):
            return original(instance, url, **kwargs)
        assert url == 'https://authority.example/api/v1/auth/authorization'
        if mode['error']:
            raise httpx.ConnectError('private upstream failure')
        result = client.get('/api/v1/auth/authorization', headers=kwargs['headers'])
        data = result.json()
        if mode['snapshot'] != 'actual' and result.status_code == 200:
            if mode['snapshot'] == 'missing':
                data['data'].pop('capability_lifecycle', None)
            else:
                data['data']['capability_lifecycle'] = mode['snapshot']
        return httpx.Response(result.status_code, json=data, request=httpx.Request('GET', url))

    async def forward(instance, method, url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={'data': {'accepted': True}}, request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, 'get', get)
    monkeypatch.setattr(httpx.AsyncClient, 'request', forward)
    return TestClient(create_app('tool-gateway', settings)), calls, mode


def test_restart_and_two_gateways_obey_live_controls(authority, monkeypatch):
    client, app, settings = authority
    tool, calls, _ = gateway(authority, monkeypatch)
    other = TestClient(create_app('tool-gateway', settings))
    payload = {'expression': 'SELECT /demo'}
    assert tool.post('/api/v1/tools/xelibrary.query', headers=auth(), json=payload).status_code == 200
    assert len(calls) == 1
    result = client.patch(PATH, headers=auth('admin'), json=patch())
    assert result.status_code == 200, result.text
    assert result.json()['data']['generation'] == 1
    for token in ('alice', 'bob'):
        for c in (tool, other):
            denied = c.post('/api/v1/tools/xelibrary.query', headers=auth(token), json=payload)
            assert denied.status_code == 404
            names = [t['function']['name'] for t in c.get('/api/v1/tools', headers=auth(token)).json()['data']]
            assert 'xelibrary.query' not in names and 'xelibrary.search' in names
        items = client.get('/api/v1/capabilities', headers=auth(token)).json()['data']
        assert QUERY_CAPABILITY not in {x['id'] for x in items}
        assert client.get('/api/v1/capabilities/' + QUERY_CAPABILITY, headers=auth(token)).status_code == 404
    assert len(calls) == 1
    restarted = TestClient(desktop_app(settings))
    assert restarted.get(PATH, headers=auth('admin')).json()['data']['enabled'] is False
    assert app.state.capability_lifecycle.snapshot() == restarted.app.state.capability_lifecycle.snapshot()
    enabled = restarted.patch(PATH, headers=auth('admin'), json=patch(True, 1))
    assert enabled.status_code == 200 and enabled.json()['data']['generation'] == 2
    assert tool.post('/api/v1/tools/xelibrary.query', headers=auth(), json=payload).status_code == 200
    assert len(calls) == 2
    assert calls[-1][1]['headers']['Authorization'] == 'Bearer alice'
    assert 'X-Request-ID' in calls[-1][1]['headers']


def test_cas_idempotency_and_atomic_audit(authority):
    client, _, settings = authority
    first = patch()
    assert client.patch(PATH, headers=auth('admin'), json=first).status_code == 200
    assert client.patch(PATH, headers=auth('admin'), json=patch(True, 0)).status_code == 409
    replay = client.patch(PATH, headers=auth('admin'), json=first).json()['data']
    assert replay['duplicate'] and replay['generation'] == replay['applied_generation'] == 1
    assert client.patch(PATH, headers=auth('admin'), json={**first, 'enabled': True}).status_code == 409
    assert client.patch(PATH, headers=auth('admin'), json=patch(True, 1)).status_code == 200
    replay = client.patch(PATH, headers=auth('admin'), json=first).json()['data']
    assert replay['duplicate'] and replay['enabled'] and replay['generation'] == 2
    assert replay['applied_generation'] == 1  # Historical request cannot revert newer state.
    with connect(settings.data_dir / 'accounts.sqlite') as db:
        assert db.execute('SELECT count(*) FROM capability_lifecycle_audit').fetchone()[0] == 2


def test_concurrent_writers_only_one_generation_commits(authority):
    _, app, settings = authority
    def change(_):
        store = CapabilityLifecycle(settings.data_dir / 'accounts.sqlite')
        body = LifecyclePatch.model_validate(patch())
        try:
            return store.change(QUERY_CAPABILITY, body, 'synthetic-admin')['generation']
        except APIError as error:
            return error.code
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(change, range(6)))
    assert results.count(1) == 1
    assert results.count('LIFECYCLE_GENERATION_CONFLICT') == 5
    assert app.state.capability_lifecycle.snapshot()['revision'] == 1


def test_management_requires_dedicated_interactive_human_permission(authority):
    client, _, settings = authority
    for token in ('alice', 'bob', 'admin-only', 'agent'):
        if token == 'admin-only':
            settings.dev_tokens[token] = {'sub': token, 'scope': 'desktop:admin', 'agent_kind': 'human'}
        if token == 'agent':
            settings.dev_tokens[token] = {'sub': token, 'scope': 'capabilities:manage', 'agent_kind': 'devagent'}
        assert client.patch(PATH, headers=auth(token), json=patch()).status_code == 403
    assert client.patch(PATH, json=patch()).status_code == 401
    # The UI app/core/unknown capability cannot be made globally unavailable here.
    for identifier in ('builtin.platform.app.settings', 'builtin.platform.provider.d100m', 'unknown.capability'):
        assert client.patch(f'/api/v1/capabilities/{identifier}/lifecycle',
                            headers=auth('admin'), json=patch()).status_code == 404
    assert client.get(PATH, headers=auth('admin')).json()['data']['generation'] == 0


@pytest.mark.parametrize('body', [
    {'enabled': 'false', 'expected_generation': 0, 'operation_id': str(uuid4())},
    {'enabled': False, 'expected_generation': -1, 'operation_id': str(uuid4())},
    {'enabled': False, 'expected_generation': True, 'operation_id': str(uuid4())},
    {'enabled': False, 'expected_generation': 0, 'operation_id': 'not-uuid'},
    {**patch(), 'scope': 'deploy:execute'},
])
def test_management_body_is_strict(authority, body):
    client, _, _ = authority
    assert client.patch(PATH, headers=auth('admin'), json=body).status_code == 422


@pytest.mark.parametrize('snapshot', [
    'missing', None, {}, {'version': True, 'revision': 1, 'disabled': []},
    {'version': 2, 'revision': 1, 'disabled': []},
    {'version': 1, 'revision': -1, 'disabled': []},
    {'version': 1, 'revision': 1, 'disabled': 'bad'},
    {'version': 1, 'revision': 1, 'disabled': [QUERY_CAPABILITY]*2},
    {'version': 1, 'revision': 1, 'disabled': ['unsupported.capability']},
    {'version': 1, 'revision': 0, 'disabled': [QUERY_CAPABILITY]},
])
def test_strict_remote_snapshot_cannot_fall_back_to_jwt(authority, monkeypatch, snapshot):
    tool, calls, mode = gateway(authority, monkeypatch)
    mode['snapshot'] = snapshot
    result = tool.post('/api/v1/tools/xelibrary.query', headers=auth(), json={'expression': 'test'})
    assert result.status_code == 503
    assert not calls


def test_authority_failure_missing_configuration_and_scope_revocation(authority, monkeypatch):
    _, _, settings = authority
    tool, calls, mode = gateway(authority, monkeypatch)
    mode['error'] = True
    result = tool.post('/api/v1/tools/xelibrary.query', headers=auth(), json={'expression': 'test'})
    assert result.status_code == 503 and 'private upstream' not in result.text
    mode['error'] = None
    settings.dev_tokens['alice']['scope'] = 'desktop:read'
    assert tool.post('/api/v1/tools/xelibrary.query', headers=auth(), json={'expression': 'test'}).status_code == 403
    assert not calls
    monkeypatch.delenv('AUTHORIZATION_URL')
    unconfigured = TestClient(create_app('tool-gateway', settings))
    assert unconfigured.get('/api/v1/tools', headers=auth('bob')).status_code == 503


def test_rollout_off_blocks_mutation_but_preserves_persisted_denial(authority, monkeypatch):
    client, _, settings = authority
    assert client.patch(PATH, headers=auth('admin'), json=patch()).status_code == 200
    monkeypatch.setenv('CAPABILITY_LIFECYCLE_ENABLED', '0')
    restarted = TestClient(desktop_app(settings))
    assert restarted.patch(PATH, headers=auth('admin'), json=patch(True, 1)).status_code == 503
    assert restarted.get('/api/v1/capabilities/' + QUERY_CAPABILITY, headers=auth()).status_code == 404
    tool, calls, _ = gateway((restarted, restarted.app, settings), monkeypatch)
    assert tool.post('/api/v1/tools/xelibrary.query', headers=auth(), json={'expression': 'test'}).status_code == 404
    assert not calls


def test_corrupt_persistence_is_unavailable_not_default_enabled(authority):
    client, _, settings = authority
    with connect(settings.data_dir / 'accounts.sqlite') as db:
        db.execute('DELETE FROM capability_lifecycle_meta')
        db.commit()
    result = client.get('/api/v1/auth/authorization', headers=auth())
    assert result.status_code == 503
    assert result.json()['error']['code'] == 'CAPABILITY_LIFECYCLE_UNAVAILABLE'


def test_snapshot_no_secret_or_publisher_state_and_flag_validation(settings, monkeypatch):
    assert parse_snapshot({'version': 1, 'revision': 0, 'disabled': []}) == (0, frozenset())
    monkeypatch.setenv('CAPABILITY_LIFECYCLE_ENABLED', 'yes')
    with pytest.raises(ValueError, match='must be 0 or 1'):
        create_app('tool-gateway', settings)


def test_api_key_cannot_manage_even_with_explicit_management_scope(authority):
    client, _, _ = authority
    assert client.post('/api/v1/auth/accounts/bootstrap', headers=auth('admin'), json={}).status_code == 201
    key_result = client.post('/api/v1/auth/api-keys', headers=auth('admin'), json={
        'owner': 'admin', 'name': 'synthetic-control-key', 'scopes': ['capabilities:manage'],
    })
    assert key_result.status_code == 201
    key = key_result.json()['data']['token']
    denied = client.patch(PATH, headers=auth(key), json=patch())
    assert denied.status_code == 403
    assert denied.json()['error']['code'] == 'INTERACTIVE_LOGIN_REQUIRED'


def test_audit_failure_rolls_back_state_and_generation(authority):
    client, _, settings = authority
    database = settings.data_dir / 'accounts.sqlite'
    with connect(database) as db:
        db.execute("CREATE TRIGGER fail_lifecycle_audit BEFORE INSERT ON capability_lifecycle_audit "
                   "BEGIN SELECT RAISE(ABORT, 'synthetic write failure'); END")
        db.commit()
    body = patch()
    result = client.patch(PATH, headers=auth('admin'), json=body)
    assert result.status_code == 503
    state = client.get(PATH, headers=auth('admin')).json()['data']
    assert state['generation'] == 0 and state['enabled']
    with connect(database) as db:
        assert db.execute('SELECT revision FROM capability_lifecycle_meta').fetchone()[0] == 0
        assert db.execute('SELECT count(*) FROM capability_lifecycle_audit').fetchone()[0] == 0
        db.execute('DROP TRIGGER fail_lifecycle_audit')
        db.commit()
    assert client.patch(PATH, headers=auth('admin'), json=body).json()['data']['generation'] == 1


def test_state_and_discovery_are_not_http_cacheable(authority, monkeypatch):
    client, _, _ = authority
    tool, _, _ = gateway(authority, monkeypatch)
    for c, endpoint, token in (
        (client, PATH, 'admin'), (client, '/api/v1/capabilities', 'alice'),
        (client, '/api/v1/auth/authorization', 'alice'), (tool, '/api/v1/tools', 'alice'),
    ):
        result = c.get(endpoint, headers=auth(token))
        assert result.status_code == 200
        assert result.headers['Cache-Control'] == 'no-store'
