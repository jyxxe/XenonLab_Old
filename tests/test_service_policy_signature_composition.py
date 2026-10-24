"""Integration-only evidence: two independent current authorities, no admission."""

from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from xeonolab.service_execution_policy import ExecutionPolicyError, resolve_execution_policy
from xeonolab.service_plugin_signatures import verify_service_signature
from xeonolab.service_plugin_trust import ServiceSignatureError, TrustPolicyStore, digest

from .test_service_execution_policy import (
    MANIFEST,
    NOW,
    POLICY,
    declaration,
    encoded,
    publish,
)
from .test_service_execution_policy import (
    authority as authority_fixture,
)
from .test_service_plugin_signatures import envelope, public
from .test_service_plugin_signatures import policy as signing_policy

authority = authority_fixture


def trusted(authority, tmp_path):
    store = TrustPolicyStore(
        tmp_path / "trust.sqlite", clock=lambda: NOW, management_enabled=True, initialize=True
    )
    key = Ed25519PrivateKey.generate()
    store.publish(
        encoded(signing_policy([key])), expected_revision=0, operation_id=uuid4(), request=authority[1]()
    )
    return store, key


def test_signature_is_not_permission_to_use_replaced_or_disabled_policy(authority, tmp_path):
    publish(authority)
    trust, key = trusted(authority, tmp_path)
    raw = declaration(encoded(POLICY))
    signed = envelope(payload=raw, keys=[key])
    signature = verify_service_signature(signed, MANIFEST, trust)
    result = resolve_execution_policy(raw, MANIFEST, authority[0])
    assert signature.declaration_digest == result.declaration_digest
    assert not hasattr(result, "admission_granted") and not hasattr(signature, "admission_granted")
    replacement = {**POLICY, "limits": {**POLICY["limits"], "memory_bytes": 256 * 1024**2}}
    publish(authority, replacement, revision=1)
    # A real signature still authenticates the old bytes; it cannot satisfy the
    # independent current policy binding after a resource-profile change.
    verify_service_signature(signed, MANIFEST, trust)
    with pytest.raises(ExecutionPolicyError):
        resolve_execution_policy(raw, MANIFEST, authority[0])
    new_raw = declaration(encoded(replacement))
    verify_service_signature(envelope(payload=new_raw, keys=[key]), MANIFEST, trust)
    resolve_execution_policy(new_raw, MANIFEST, authority[0])
    authority[0].disable("lookup-policy", expected_revision=2, operation_id=uuid4(), request=authority[1]())
    with pytest.raises(ExecutionPolicyError):
        resolve_execution_policy(new_raw, MANIFEST, authority[0])


def test_current_policy_cannot_replace_revoked_signer_evidence(authority, tmp_path):
    publish(authority)
    trust, key = trusted(authority, tmp_path)
    raw = declaration(encoded(POLICY))
    signed = envelope(payload=raw, keys=[key])
    verify_service_signature(signed, MANIFEST, trust)
    resolve_execution_policy(raw, MANIFEST, authority[0])
    trust.publish(
        encoded(signing_policy([], revoked_keys=[digest(public(key))])),
        expected_revision=1,
        operation_id=uuid4(),
        request=authority[1](),
    )
    assert resolve_execution_policy(raw, MANIFEST, authority[0]).policy_revision == 1
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(signed, MANIFEST, trust)


def test_signed_foreign_dependency_stays_unresolved_and_unregistered(authority, tmp_path):
    import json
    import socket
    import subprocess
    import urllib.request

    from xeonolab.capabilities import load_registry

    publish(authority)
    trust, key = trusted(authority, tmp_path)
    manifest = json.loads(MANIFEST)
    manifest["capabilities"][0]["dependencies"] = ["other.service.provider@^1.0.0"]
    manifest_bytes = encoded(manifest)
    raw = declaration(encoded(POLICY), manifest_bytes)
    before = load_registry().all()

    def forbidden(*args, **kwargs):
        raise AssertionError("resolution must not fetch or execute dependencies")

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(socket, "create_connection", forbidden)
        patch.setattr(urllib.request, "urlopen", forbidden)
        patch.setattr(subprocess, "Popen", forbidden)
        signature = verify_service_signature(envelope(payload=raw, keys=[key]), manifest_bytes, trust)
        policy = resolve_execution_policy(raw, manifest_bytes, authority[0])
    assert signature.capability_manifest_digest == policy.capability_manifest_digest == digest(manifest_bytes)
    assert not hasattr(policy, "admission_granted")
    assert load_registry().lookup("other.service.provider") is None
    assert load_registry().all() == before
