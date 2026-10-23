"""Corrupt account snapshots cannot take down desktop discovery."""
import copy
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from xeonolab.services import create_app

from .conftest import auth
from .test_plugins import MANIFEST, desktop_settings, publish


@pytest.mark.parametrize('damage', [
    '{broken', '[]', 'null',
    json.dumps({**MANIFEST, 'permissions': 1}),
    json.dumps({**MANIFEST, 'permissions': ['xelibrary:read', 1]}),
    json.dumps({**MANIFEST, 'id': 'another-plugin'}),
    json.dumps({**MANIFEST, 'version': 'bad'}),
    json.dumps({**MANIFEST, 'app': {'id': 'hello-app'}}),
])
def test_corrupt_snapshot_isolated_and_explicitly_repairable(settings, damage):
    client = TestClient(create_app('desktop-web', desktop_settings(settings)))
    assert publish(client).status_code == 201
    healthy = copy.deepcopy(MANIFEST)
    healthy['id'] = 'healthy-plugin'
    healthy['app']['id'] = 'healthy-app'
    healthy['widget']['id'] = 'healthy-widget'
    assert publish(client, healthy).status_code == 201
    for token in ('alice', 'bob'):
        for plugin in ('hello-plugin', 'healthy-plugin'):
            assert client.post(f'/api/v1/plugins/{plugin}/install', headers=auth(token)).status_code == 200

    def corrupt():
        with sqlite3.connect(settings.data_dir / 'desktop.sqlite') as db:
            db.execute('UPDATE plugin_installs SET manifest=? WHERE owner=? AND plugin_id=?',
                       (damage, 'alice', 'hello-plugin'))

    builtin_before = {
        item['id'] for item in client.get('/api/v1/capabilities', headers=auth()).json()['data']
        if item['source'] == 'builtin'
    }
    corrupt()
    for endpoint, absent, present in (
        ('capabilities', 'plugin.hello-plugin.app', 'plugin.healthy-plugin.app'),
        ('apps', 'hello-app', 'healthy-app'),
        ('users/me/plugin-widgets', 'hello-widget', 'healthy-widget'),
    ):
        result = client.get('/api/v1/' + endpoint, headers=auth())
        assert result.status_code == 200, result.text
        ids = {item['id'] for item in result.json()['data']}
        assert absent not in ids and present in ids
        if endpoint == 'capabilities':
            assert builtin_before <= ids
        bob = client.get('/api/v1/' + endpoint, headers=auth('bob'))
        assert bob.status_code == 200
        assert absent in {item['id'] for item in bob.json()['data']}
    for endpoint in ('apps/hello-app', 'capabilities/plugin.hello-plugin.app'):
        assert client.get('/api/v1/' + endpoint, headers=auth()).status_code == 404
    catalog = client.get('/api/v1/plugins', headers=auth())
    assert catalog.status_code == 200
    item = next(x for x in catalog.json()['data'] if x['id'] == 'hello-plugin')
    assert item['installed'] and not item['installed_app'] and not item['installed_widget']
    with sqlite3.connect(settings.data_dir / 'desktop.sqlite') as db:
        assert db.execute('SELECT manifest FROM plugin_installs WHERE owner=? AND plugin_id=?',
                          ('alice', 'hello-plugin')).fetchone()[0] == damage
    endpoint = '/api/v1/plugins/hello-plugin/install'
    assert client.patch(endpoint, headers=auth(), json={'enabled': False}).status_code == 200
    denied = client.patch(endpoint, headers=auth(), json={'enabled': True})
    assert denied.status_code == 409
    assert denied.json()['error']['code'] == 'PLUGIN_SNAPSHOT_INVALID'
    assert client.post(endpoint, headers=auth()).status_code == 200
    assert client.get('/api/v1/apps/hello-app', headers=auth()).status_code == 200
    corrupt()
    assert client.delete(endpoint, headers=auth()).status_code == 200
