"""Current signer/builder expectations must bind a real signature to source and artifact."""

import base64
import copy
import json
import sqlite3
import subprocess
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from xeonolab import service_plugin_signatures as signatures
from xeonolab.common import APIError
from xeonolab.service_plugin_contract import ServicePluginContractError, validate_service_plugin
from xeonolab.service_plugin_provenance import (
    MAX_PROVENANCE_BYTES,
    PAYLOAD_TYPE,
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    verify_service_provenance,
)
from xeonolab.service_plugin_signatures import MAX_ENVELOPE_BYTES, verify_service_signature
from xeonolab.service_plugin_trust import (
    SOURCE_BUILD_TYPE,
    ServiceSignatureError,
    TrustPolicyStore,
    digest,
    parse_policy,
    trust_policy_schema,
)

from .test_service_plugin_contract import DECLARATION, MANIFEST
from .test_service_plugin_signatures import NOW, encoded, envelope, policy, public, publish
from .test_service_plugin_signatures import authority as authority_fixture

authority = authority_fixture
BUILDER = 'https://builder.example/build-platform/v1'
REPOSITORY = 'https://source.example/plugins.git'
CONFIG = 'services/example/Dockerfile'


def rule(**overrides):
    return {'builder_id': BUILDER, 'repository': REPOSITORY, 'build_config': CONFIG,
            'plugin_ids': ['example.service'], **overrides}


def build_policy(keys, **overrides):
    data = policy(keys, **overrides)
    for key in data['keys']:
        key['provenance_builders'] = [rule()]
    return data


def statement():
    declaration = json.loads(DECLARATION)
    return {'_type': STATEMENT_TYPE,
            'subject': [{'name': 'arbitrary-name-is-not-identity',
                         'digest': {'sha256': declaration['artifact_digest'][7:]}},
                        {'digest': {'sha256': digest(MANIFEST)[7:]}}],
            'predicateType': PREDICATE_TYPE,
            'predicate': {'buildDefinition': {
                'buildType': SOURCE_BUILD_TYPE,
                'externalParameters': {'repository': REPOSITORY, 'ref': declaration['release_commit'],
                                       'config': CONFIG},
                'resolvedDependencies': [{'uri': 'git+' + REPOSITORY + '@' + declaration['release_commit'],
                                          'digest': {'gitCommit': declaration['release_commit']}}]},
                          'runDetails': {'builder': {'id': BUILDER}}}}


def signed(data, keys, **kwargs):
    return envelope(payload=encoded(data), keys=keys, payload_type=PAYLOAD_TYPE, **kwargs)


def verify(authority, data, keys, **kwargs):
    return verify_service_provenance(signed(data, keys), DECLARATION, MANIFEST, authority[0], **kwargs)


@pytest.mark.parametrize(('url', 'keyids'), [(False, True), (True, False)])
def test_real_provenance_exact_bytes_and_frozen_evidence(authority, url, keyids):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    raw = encoded(statement())
    result = verify_service_provenance(envelope(payload=raw, keys=[key], payload_type=PAYLOAD_TYPE,
                                               url=url, keyids=keyids), DECLARATION, MANIFEST, authority[0])
    declaration = json.loads(DECLARATION)
    assert result.statement_digest == digest(raw)
    assert result.declaration_digest == digest(DECLARATION)
    assert result.artifact_digest == declaration['artifact_digest']
    assert result.capability_manifest_digest == digest(MANIFEST)
    assert result.release_commit == declaration['release_commit']
    assert result.builder_id == BUILDER and result.repository == REPOSITORY and result.build_config == CONFIG
    assert result.trust_revision == 1 and result.checked_at == NOW and result.valid_until == NOW + 300
    assert result.signers == (digest(public(key)),)
    assert not hasattr(result, 'admission_granted') and not hasattr(result, 'slsa_level')
    with pytest.raises(FrozenInstanceError):
        result.release_commit = 'a' * 40
    # Same JSON, different exact signed bytes => different proof identity.
    changed = verify_service_provenance(envelope(payload=raw + b' ', keys=[key], payload_type=PAYLOAD_TYPE),
                                         DECLARATION, MANIFEST, authority[0])
    assert changed.statement_digest != result.statement_digest


def test_publisher_only_key_is_not_an_authorized_builder_and_purposes_are_distinct(authority):
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    verify_service_signature(envelope(keys=[key]), MANIFEST, authority[0])
    with pytest.raises(ServiceSignatureError):
        verify(authority, statement(), [key])
    publish(authority, build_policy([key]), revision=1)
    verify(authority, statement(), [key])
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(signed(statement(), [key]), MANIFEST, authority[0])
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(envelope(keys=[key]), DECLARATION, MANIFEST, authority[0])


@pytest.mark.parametrize('field', ['builder_id', 'repository', 'build_config', 'plugin_ids'])
def test_signer_builder_namespace_and_source_rules_cannot_be_substituted(authority, field):
    key = Ed25519PrivateKey.generate()
    data = build_policy([key])
    data['keys'][0]['provenance_builders'][0][field] = {
        'builder_id': 'https://other.example/builder', 'repository': 'https://other.example/repo.git',
        'build_config': 'other/Dockerfile', 'plugin_ids': ['other.service'],
    }[field]
    if field == 'plugin_ids':
        data['keys'][0]['plugin_ids'].append('other.service')
    publish(authority, data)
    with pytest.raises(ServiceSignatureError):
        verify(authority, statement(), [key])


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(_type='https://in-toto.io/Statement/v0.1'),
    lambda d: d.update(predicateType='https://slsa.dev/provenance/v0.2'),
    lambda d: d.update(subject=[]),
    lambda d: d.update(subject=None),
    lambda d: d['subject'][0].update(digest={'sha256': 'a' * 64}),
    lambda d: d['subject'][0].update(digest={'md5': '0' * 32}),
    lambda d: d['subject'].append(copy.deepcopy(d['subject'][0])),
    lambda d: d['subject'][0].update(digest=[]),
    lambda d: d.update(predicate=None),
    lambda d: d['predicate'].update(runDetails=None),
    lambda d: d['predicate']['runDetails'].update(builder={}),
    lambda d: d['predicate']['runDetails']['builder'].update(id='https://other.example/builder'),
    lambda d: d['predicate']['buildDefinition'].update(buildType='https://other.example/buildtype'),
    lambda d: d['predicate']['buildDefinition'].update(externalParameters=None),
    lambda d: d['predicate']['buildDefinition']['externalParameters'].update(ref='a' * 40),
    lambda d: d['predicate']['buildDefinition']['externalParameters'].update(repository='https://fork.example/repo'),
    lambda d: d['predicate']['buildDefinition']['externalParameters'].update(config='alternative/Dockerfile'),
    lambda d: d['predicate']['buildDefinition']['externalParameters'].update(commands='secret-do-not-echo'),
    lambda d: d['predicate']['buildDefinition']['externalParameters'].update(ref=True),
    lambda d: d['predicate']['buildDefinition'].update(resolvedDependencies=[]),
    lambda d: d['predicate']['buildDefinition'].update(resolvedDependencies=None),
    lambda d: d['predicate']['buildDefinition']['resolvedDependencies'][0].update(uri='git+https://fork.example/repo'),
    lambda d: d['predicate']['buildDefinition']['resolvedDependencies'][0].update(digest={'gitCommit': 'a' * 40}),
    lambda d: d['predicate']['buildDefinition']['resolvedDependencies'].append(
        copy.deepcopy(d['predicate']['buildDefinition']['resolvedDependencies'][0])),
])
def test_signed_but_unexpected_or_ambiguous_claims_are_denied(authority, mutation):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    data = statement()
    mutation(data)
    with pytest.raises(ServiceSignatureError) as error:
        verify(authority, data, [key])
    assert str(error.value) == 'service provenance verification failed'


def test_standard_extensions_ignored_but_build_interface_is_exact(authority):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    data = statement()
    original = verify(authority, data, [key])
    data['vendor_extension'] = {'never-execute': 'https://not-fetched.example/token'}
    data['subject'][0].update(name=None, uri=None, annotations={'future': True}, future_field='ignored')
    predicate = data['predicate']
    predicate.update(vendor_extension=None)
    predicate['runDetails'].update(metadata=None, byproducts=[], vendor_extension={'key': 'value'})
    predicate['runDetails']['builder'].update(version={}, builderDependencies=None, future='ignored')
    predicate['buildDefinition'].update(internalParameters=None, future='ignored')
    predicate['buildDefinition']['resolvedDependencies'].append({'uri': 'other:dependency', 'digest': {'sha256': 'b' * 64}})
    result = verify(authority, data, [key])
    assert result.signers == original.signers and result.statement_digest != original.statement_digest
    # Unrecognized external parameters are a different security boundary.
    predicate['buildDefinition']['externalParameters']['future'] = None
    with pytest.raises(ServiceSignatureError):
        verify(authority, data, [key])


def test_threshold_counts_unique_current_builder_eligible_signers_only(authority):
    keys = [Ed25519PrivateKey.generate() for _ in range(3)]
    data = build_policy(keys, threshold=2)
    data['keys'][2]['provenance_builders'] = []
    publish(authority, data)
    assert len(verify(authority, statement(), keys).signers) == 2
    for candidates in ([keys[0]], [keys[0], keys[0]], [keys[0], keys[2]]):
        with pytest.raises(ServiceSignatureError):
            verify(authority, statement(), candidates)


@pytest.mark.parametrize('kind', ['key', 'artifact', 'builder'])
def test_revocation_and_removed_builder_deny_previous_proof(authority, kind):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    verify(authority, statement(), [key])
    data = build_policy([key])
    if kind == 'key':
        data['revoked_keys'] = [digest(public(key))]
        data['keys'] = []
    elif kind == 'artifact':
        data['revoked_artifacts'] = [json.loads(DECLARATION)['artifact_digest']]
    else:
        data['keys'][0]['provenance_builders'] = []
    publish(authority, data, revision=1)
    with pytest.raises(ServiceSignatureError):
        verify(authority, statement(), [key])
    # Omitting tombstones cannot resurrect permanently revoked keys/artifacts.
    if kind == 'key':
        with pytest.raises(APIError) as error:
            publish(authority, build_policy([key]), revision=2)
        assert error.value.code == 'TRUST_KEY_REVOKED'
        publish(authority, build_policy([]), revision=2)
        assert digest(public(key)) in authority[0].snapshot().revoked_keys
        with pytest.raises(ServiceSignatureError):
            verify(authority, statement(), [key])
    elif kind == 'artifact':
        publish(authority, build_policy([key]), revision=2)
        with pytest.raises(ServiceSignatureError):
            verify(authority, statement(), [key])


@pytest.mark.parametrize('change', ['revision', 'expiry', 'key-expiry'])
def test_current_authority_and_clock_rechecked_after_crypto(authority, monkeypatch, change):
    key = Ed25519PrivateKey.generate()
    data = build_policy([key])
    data['keys'][0]['expires_at'] = NOW + 20
    publish(authority, data)
    original = signatures._valid_signature
    triggered = False

    def race(*args):
        nonlocal triggered
        valid = original(*args)
        if not triggered:
            triggered = True
            if change == 'revision':
                publish(authority, build_policy([key]), revision=1)
            else:
                authority[2][0] = NOW + (301 if change == 'expiry' else 21)
        return valid

    monkeypatch.setattr(signatures, '_valid_signature', race)
    with pytest.raises(ServiceSignatureError):
        verify(authority, statement(), [key])


@pytest.mark.parametrize('encoding', ['utf-16-le', 'utf-16-be', 'utf-32-le', 'utf-32-be'])
def test_json_autodetection_cannot_bypass_utf8_contract(authority, encoding):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    with pytest.raises(ServicePluginContractError):
        validate_service_plugin(DECLARATION.decode().encode(encoding), MANIFEST)
    manifest = MANIFEST.decode().encode(encoding)
    declaration = json.loads(DECLARATION)
    declaration['capability_manifest_digest'] = digest(manifest)
    with pytest.raises(ServicePluginContractError):
        validate_service_plugin(encoded(declaration), manifest)
    with pytest.raises(ServiceSignatureError):
        parse_policy(encoded(build_policy([key])).decode().encode(encoding))
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(envelope(payload=encoded(statement()).decode().encode(encoding), keys=[key],
                                           payload_type=PAYLOAD_TYPE), DECLARATION, MANIFEST, authority[0])
    # Envelope and old declaration verification share the hardened decoder too.
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[key]).decode().encode(encoding), MANIFEST, authority[0])
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(payload=DECLARATION.decode().encode(encoding), keys=[key]),
                                 MANIFEST, authority[0])


@pytest.mark.parametrize('field,value', [
    ('builder_id', 'http://untrusted.example/builder'),
    ('builder_id', 'https://user:secret@builder.example/v1'),
    ('repository', 'https://source.example/repo?credential=secret'),
    ('repository', 'https://source.example/repo#mutable'),
    ('repository', 'https://source.example:99999/repo'),
    ('repository', 'https://source.example/repo\n'),
    ('repository', 'file:///host/private'),
    ('build_config', '/etc/secret'), ('build_config', '../Dockerfile'),
    ('build_config', 'path/../Dockerfile'), ('build_config', './Dockerfile'),
    ('build_config', 'path//Dockerfile'), ('build_config', 'Dockerfile;command'),
    ('plugin_ids', ['builtin.platform']), ('plugin_ids', ['other.service']),
    ('plugin_ids', ['example.service', 'example.service']),
])
def test_core_builder_rules_are_bounded_and_cannot_expand_namespace(field, value):
    data = build_policy([Ed25519PrivateKey.generate()])
    data['keys'][0]['provenance_builders'][0][field] = value
    with pytest.raises(ServiceSignatureError):
        parse_policy(encoded(data))


def test_duplicate_and_unbounded_builder_rules_rejected_and_legacy_policy_unchanged():
    key = Ed25519PrivateKey.generate()
    original = parse_policy(encoded(policy([key])))
    assert original.keys[0].provenance_builders == ()
    data = build_policy([key])
    data['keys'][0]['provenance_builders'] *= 2
    with pytest.raises(ServiceSignatureError):
        parse_policy(encoded(data))
    data['keys'][0]['provenance_builders'] = [rule(builder_id=f'https://builder.example/{i}') for i in range(9)]
    with pytest.raises(ServiceSignatureError):
        parse_policy(encoded(data))


def test_rules_use_existing_human_gate_cas_idempotence_and_audit(authority):
    key = Ed25519PrivateKey.generate()
    data = build_policy([key])
    with pytest.raises(APIError) as error:
        publish(authority, data, token='agent')
    assert error.value.status == 403
    operation = uuid4()
    publish(authority, data, operation=operation)
    assert publish(authority, data, operation=operation)['duplicate'] is True
    with pytest.raises(APIError) as error:
        publish(authority, data)  # Stale expected revision 0.
    assert error.value.status == 409
    with sqlite3.connect(authority[0].path) as db:
        assert db.execute('SELECT count(*) FROM service_trust_audit').fetchone()[0] == 1
    authority[0].management_enabled = False
    with pytest.raises(APIError):
        publish(authority, data, revision=1)
    verify(authority, statement(), [key])  # Management gate is not a verifier bypass.


@pytest.mark.parametrize('case', ['payload', 'envelope', 'subjects', 'materials', 'json', 'revision', 'missing-db'])
def test_limits_and_unavailable_authority_deny(authority, tmp_path, case):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    data = statement()
    store, kwargs = authority[0], {}
    raw = signed(data, [key])
    if case == 'payload':
        raw = envelope(payload=b' ' * (MAX_PROVENANCE_BYTES + 1), keys=[key], payload_type=PAYLOAD_TYPE)
    elif case == 'envelope':
        raw = b' ' * (MAX_ENVELOPE_BYTES + 1)
    elif case == 'subjects':
        data['subject'] *= 17
        raw = signed(data, [key])
    elif case == 'materials':
        data['predicate']['buildDefinition']['resolvedDependencies'] *= 65
        raw = signed(data, [key])
    elif case == 'json':
        raw = envelope(payload=b'{"_type":1,"_type":2}', keys=[key], payload_type=PAYLOAD_TYPE)
    elif case == 'revision':
        kwargs['minimum_revision'] = 2
    else:
        store = TrustPolicyStore(tmp_path / 'missing.sqlite')
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(raw, DECLARATION, MANIFEST, store, **kwargs)
    if case == 'missing-db':
        assert not store.path.exists()


def test_schema_extended_and_existing_readonly_cli_safe(authority, tmp_path):
    import time

    from jsonschema import Draft202012Validator

    key = Ed25519PrivateKey.generate()
    now = int(time.time())
    authority[2][0] = now
    data = build_policy([key], issued_at=now - 10, expires_at=now + 300)
    data['keys'][0].update(not_before=now - 10, expires_at=now + 600)
    schema = trust_policy_schema()
    Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(data)
    assert Draft202012Validator(schema).is_valid(policy([key]))
    publish(authority, data)
    paths = []
    for name, value in [('envelope.json', signed(statement(), [key])),
                        ('declaration.json', DECLARATION), ('manifest.json', MANIFEST)]:
        path = tmp_path / name
        path.write_bytes(value)
        paths.append(str(path))
    command = [sys.executable, 'scripts/verify_service_provenance.py', *paths,
               '--trust-db', str(authority[0].path)]
    with sqlite3.connect(authority[0].path) as db:
        before = list(db.iterdump())
    result = subprocess.run(command, capture_output=True, text=True, timeout=15,
                            cwd=Path(__file__).resolve().parents[1])
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output['provenance_verified'] is True and output['admission_granted'] is False
    assert output['trust_revision'] == 1
    with sqlite3.connect(authority[0].path) as db:
        assert list(db.iterdump()) == before
    Path(paths[0]).write_bytes(b'secret-do-not-echo')
    failed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert failed.returncode == 1 and 'secret-do-not-echo' not in failed.stderr + failed.stdout


def test_verification_never_fetches_build_urls_or_runs_config(authority, monkeypatch):
    import socket
    import urllib.request

    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))

    def forbidden(*args, **kwargs):
        pytest.fail('source verification attempted network or process execution')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    verify(authority, statement(), [key])


def test_build_proof_binds_exact_capability_manifest_bytes_too(authority):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    data = statement()
    raw = signed(data, [key])
    verify_service_provenance(raw, DECLARATION, MANIFEST, authority[0])
    substituted = MANIFEST + b' '
    declaration = json.loads(DECLARATION)
    declaration['capability_manifest_digest'] = digest(substituted)
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(raw, encoded(declaration), substituted, authority[0])


@pytest.mark.parametrize('payload_type', ['application/vnd.in-toto+json', 'application/vnd.in-toto.provenance+json'])
def test_standard_media_types_are_signed_and_predicate_checked_independently(authority, payload_type):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    proof = envelope(payload=encoded(statement()), keys=[key], payload_type=payload_type)
    verify_service_provenance(proof, DECLARATION, MANIFEST, authority[0])
    substituted = json.loads(proof)
    substituted['payloadType'] = ('application/vnd.in-toto+json' if payload_type.endswith('.provenance+json')
                                  else 'application/vnd.in-toto.provenance+json')
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(encoded(substituted), DECLARATION, MANIFEST, authority[0])


def test_payload_signature_and_declaration_commit_substitution_deny(authority):
    key = Ed25519PrivateKey.generate()
    publish(authority, build_policy([key]))
    proof = signed(statement(), [key])
    declaration = json.loads(DECLARATION)
    declaration['release_commit'] = 'a' * 40
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(proof, encoded(declaration), MANIFEST, authority[0])
    data = json.loads(proof)
    data['payload'] = base64.b64encode(encoded(statement()) + b' ').decode()
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(encoded(data), DECLARATION, MANIFEST, authority[0])
    data = json.loads(proof)
    value = bytearray(base64.b64decode(data['signatures'][0]['sig']))
    value[0] ^= 1
    data['signatures'][0]['sig'] = base64.b64encode(value).decode()
    with pytest.raises(ServiceSignatureError):
        verify_service_provenance(encoded(data), DECLARATION, MANIFEST, authority[0])
