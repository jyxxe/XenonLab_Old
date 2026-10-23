"""Offline declaration validation grants no admission, trust or execution."""

import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from xeonolab.capabilities import load_registry
from xeonolab.service_plugin_contract import (
    MAX_DECLARATION_BYTES,
    MAX_MANIFEST_BYTES,
    ServicePluginContractError,
    declaration_schema,
    validate_service_plugin,
)

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / 'infra/plugins/examples'
DECLARATION = (EXAMPLES / 'service-plugin-declaration.json').read_bytes()
MANIFEST = (EXAMPLES / 'service-capabilities.json').read_bytes()


def encoded(value):
    return json.dumps(value).encode()


def check_manifest(manifest):
    raw = encoded(manifest)
    declaration = json.loads(DECLARATION)
    declaration['capability_manifest_digest'] = 'sha256:' + hashlib.sha256(raw).hexdigest()
    return validate_service_plugin(encoded(declaration), raw)


def test_example_is_offline_and_never_changes_the_live_registry(monkeypatch):
    import socket
    import urllib.request

    import httpx

    before = load_registry().all()

    def forbidden(*args, **kwargs):
        pytest.fail('offline validation attempted IO beyond explicitly supplied bytes')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(httpx.Client, 'send', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    result = validate_service_plugin(DECLARATION, MANIFEST)
    assert result.plugin_id == 'example.service'
    assert result.artifact_digest == 'sha256:' + '0' * 64  # Not proof artifact exists or is trusted.
    assert load_registry().all() == before
    assert not any(c['id'].startswith('example.service.') for c in before['capabilities'])


def test_generated_schema_is_checked_in():
    actual = json.loads((ROOT / 'infra/plugins/service-plugin-declaration.schema.json').read_bytes())
    assert actual == declaration_schema()


@pytest.mark.parametrize('update', [
    {'schema_version': '2'}, {'schema_version': 1}, {'artifact_format': 'python'},
    {'artifact_digest': 'repo/image:latest'}, {'artifact_digest': 'https://untrusted.invalid/image'},
    {'artifact_digest': 'sha256:' + 'A' * 64}, {'release_commit': 'main'},
    {'plugin_id': 'Other Namespace'}, {'version': '1.0'},
    {'execution_policy': {'id': 'example-readonly'}},
    {'execution_policy': {'id': 'example-readonly', 'digest': 'sha256:' + '0'*64, 'privileged': True}},
    {'command': ['sh', '-c', 'untrusted']}, {'url': 'https://untrusted.invalid'},
    {'env': {'key': 'SENTINEL_PRIVATE_VALUE'}}, {'approved': True}, {'deploy_scope': 'deploy:execute'},
])
def test_declaration_rejects_mutable_or_authority_bearing_fields(update):
    with pytest.raises(ServicePluginContractError) as error:
        validate_service_plugin(encoded({**json.loads(DECLARATION), **update}), MANIFEST)
    assert 'SENTINEL_PRIVATE_VALUE' not in str(error.value)


@pytest.mark.parametrize('update', [
    {'id': 'another.namespace'}, {'version': '2.0.0'}, {'source': 'builtin'},
    {'api_version': '99'}, {'capabilities': []}, {'capabilities': [None]},
    {'capabilities': 1}, {'permissions': ['deploy:execute']},
    {'display': {'url': 'https://untrusted.invalid'}},
])
def test_manifest_binding_and_shape_are_checked_after_digest(update):
    with pytest.raises(ServicePluginContractError):
        check_manifest({**json.loads(MANIFEST), **update})


@pytest.mark.parametrize('update', [
    {'id': 'other.namespace.tool'}, {'runtime': 'api'}, {'runtime': 'ui'},
    {'kind': 'app'}, {'kind': 'widget'}, {'kind': 'unknown'},
    {'version': 'broken'}, {'timeout_seconds': 0}, {'timeout_seconds': 301},
    {'timeout_seconds': True}, {'timeout_seconds': '5'}, {'timeout_seconds': None},
    {'permissions': ['xelibrary:read', 1]}, {'permissions': ['xelibrary:read'] * 2},
    {'permissions': ['Not a scope']}, {'dependencies': ['example.service.other']},
    {'dependencies': ['example.service.other@*']}, {'dependencies': ['example.service.other@1.0.0']*2},
    {'dependencies': ['example.service.lookup@1.0.0']},
    {'command': 'untrusted'},
])
def test_service_only_capability_policy(update):
    manifest = json.loads(MANIFEST)
    manifest['capabilities'][0].update(update)
    with pytest.raises(ServicePluginContractError):
        check_manifest(manifest)


def test_exact_manifest_bytes_and_duplicate_capabilities():
    with pytest.raises(ServicePluginContractError, match='digest mismatch'):
        validate_service_plugin(DECLARATION, MANIFEST + b'\n')
    manifest = json.loads(MANIFEST)
    manifest['capabilities'].append(copy.deepcopy(manifest['capabilities'][0]))
    with pytest.raises(ServicePluginContractError):
        check_manifest(manifest)


@pytest.mark.parametrize('raw', [b'[]', b'null', b'{broken', b'\xff',
                                b'{"schema_version":"1","schema_version":"1"}', b'{"x":NaN}',
                                b'['*2000 + b']'*2000])
def test_invalid_duplicate_and_deep_json_has_safe_diagnostics(raw):
    with pytest.raises(ServicePluginContractError):
        validate_service_plugin(raw, MANIFEST)


def test_bounded_inputs_and_manifest_duplicate_members():
    for declaration, manifest in (
        (b' '* (MAX_DECLARATION_BYTES+1), MANIFEST),
        (DECLARATION, b' '* (MAX_MANIFEST_BYTES+1)),
        (DECLARATION, b'{"id":"example.service","id":"example.service"}'),
    ):
        with pytest.raises(ServicePluginContractError):
            validate_service_plugin(declaration, manifest)


def test_cli_success_and_rejection_do_not_claim_trust_or_leak_inputs(tmp_path):
    command = [sys.executable, str(ROOT / 'scripts/validate_service_plugin.py')]
    good = subprocess.run(command + [str(EXAMPLES / 'service-plugin-declaration.json'),
                                     str(EXAMPLES / 'service-capabilities.json')],
                          capture_output=True, text=True, check=False)
    assert good.returncode == 0
    assert 'NOT verified' in good.stdout
    bad_file = tmp_path / 'bad.json'
    bad_file.write_text('{"secret":"SENTINEL_PRIVATE_VALUE"}')
    bad = subprocess.run(command + [str(bad_file), str(EXAMPLES / 'service-capabilities.json')],
                         capture_output=True, text=True, check=False)
    assert bad.returncode == 1
    assert 'SENTINEL_PRIVATE_VALUE' not in bad.stdout + bad.stderr
    assert 'Traceback' not in bad.stderr
