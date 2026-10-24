"""Real signature verification and dependency snapshots are independent gates."""

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from xeonolab.capability_snapshot import CapabilitySnapshotError, build_dependency_snapshot
from xeonolab.service_plugin_signatures import verify_service_signature
from xeonolab.service_plugin_trust import ServiceSignatureError, digest

from .test_capability_snapshot import manifest, raw
from .test_service_plugin_contract import DECLARATION, MANIFEST
from .test_service_plugin_signatures import authority as authority_fixture
from .test_service_plugin_signatures import envelope, policy, public, publish

authority = authority_fixture


def signed_dependency(authority):
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    data = json.loads(MANIFEST)
    data['capabilities'][0]['dependencies'] = ['org.dependency.tool@^1.0.0']
    manifest_bytes = raw(data)
    declaration = json.loads(DECLARATION)
    declaration['capability_manifest_digest'] = digest(manifest_bytes)
    signed = envelope(payload=raw(declaration), keys=[key])
    return key, data['capabilities'][0]['id'], manifest_bytes, signed


def test_real_signature_cannot_satisfy_missing_disabled_or_incompatible_dependency(authority):
    _, identifier, manifest_bytes, signed = signed_dependency(authority)
    evidence = verify_service_signature(signed, manifest_bytes, authority[0])
    assert evidence.capability_manifest_digest == digest(manifest_bytes)
    with pytest.raises(CapabilitySnapshotError, match='missing'):
        build_dependency_snapshot([manifest_bytes])
    with pytest.raises(CapabilitySnapshotError, match='incompatible'):
        build_dependency_snapshot([manifest_bytes, raw(manifest('org.dependency', version='2.0.0'))])
    snapshot = build_dependency_snapshot([manifest_bytes, raw(manifest('org.dependency'))],
                                         disabled=['org.dependency.tool'])
    with pytest.raises(CapabilitySnapshotError, match='disabled'):
        snapshot.resolve(identifier)
    # Graph evidence does not verify ownership/signatures of the other input.
    complete = build_dependency_snapshot([manifest_bytes, raw(manifest('org.dependency'))])
    assert len(complete.resolve(identifier).capabilities) == 2
    assert not hasattr(complete.resolve(identifier), 'admission_granted')


def test_fixed_dependency_snapshot_cannot_replace_current_signature_revocation(authority):
    key, identifier, manifest_bytes, signed = signed_dependency(authority)
    verify_service_signature(signed, manifest_bytes, authority[0])
    snapshot = build_dependency_snapshot([manifest_bytes, raw(manifest('org.dependency'))])
    original = snapshot.resolve(identifier)
    publish(authority, policy([], revoked_keys=[digest(public(key))]), revision=1)
    assert snapshot.resolve(identifier) == original  # Fixed data, deliberately no live grant.
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(signed, manifest_bytes, authority[0])
