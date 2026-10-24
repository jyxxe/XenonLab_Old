"""Real DSSE signatures and durable, current public policy; no runtime grants."""

import base64
import copy
import json
import sqlite3
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.requests import Request

from xeonolab import service_plugin_signatures as signatures
from xeonolab.common import APIError, Identity, base_app
from xeonolab.service_plugin_contract import MAX_DECLARATION_BYTES
from xeonolab.service_plugin_signatures import MAX_ENVELOPE_BYTES, PAYLOAD_TYPE, pae, verify_service_signature
from xeonolab.service_plugin_trust import (
    MAX_POLICY_BYTES,
    ServiceSignatureError,
    TrustPolicyStore,
    decode_base64,
    digest,
    trust_policy_schema,
)

from .test_service_plugin_contract import DECLARATION, MANIFEST

NOW = 2_000_000_000


def encoded(value):
    return json.dumps(value).encode()


def public(key):
    return key.public_key().public_bytes_raw()


def key_record(key, **overrides):
    data = {
        "algorithm": "ed25519",
        "public_key": base64.b64encode(public(key)).decode(),
        "plugin_ids": ["example.service"],
        "not_before": NOW - 10,
        "expires_at": NOW + 600,
    }
    return {**data, **overrides}


def policy(signing_keys, **overrides):
    return {
        "schema_version": "1",
        "issued_at": NOW - 10,
        "expires_at": NOW + 300,
        "threshold": 1,
        "keys": [key_record(key) for key in signing_keys],
        "revoked_keys": [],
        "revoked_artifacts": [],
        **overrides,
    }


def envelope(payload=DECLARATION, *, keys, keyids=True, url=False, payload_type=PAYLOAD_TYPE):
    encode = base64.urlsafe_b64encode if url else base64.b64encode

    def b64(raw):
        value = encode(raw).decode()
        return value.rstrip("=") if url else value

    entries = []
    for key in keys:
        entry = {"sig": b64(key.sign(pae(payload_type, payload)))}
        if keyids:
            entry["keyid"] = digest(public(key))
        entries.append(entry)
    return encoded({"payloadType": payload_type, "payload": b64(payload), "signatures": entries})


@pytest.fixture
def authority(settings, tmp_path):
    settings.rate_limit_enabled = False
    app = base_app("signature-test", settings)
    actors = {
        "manager": Identity("operator", {"plugin-service:manage"}, "human"),
        "agent": Identity("codex", {"plugin-service:manage"}, "codex"),
        "admin": Identity("admin", {"desktop:admin", "deploy:approve"}, "human"),
        "xek_synthetic": Identity("operator", {"plugin-service:manage"}, "human"),
    }

    def resolver(token):
        if token not in actors:
            raise APIError(401, "UNAUTHENTICATED", "invalid identity")
        return actors[token]

    app.state.authorization_resolver = resolver

    def request(token="manager"):
        headers = [(b"authorization", ("Bearer " + token).encode())] if token else []
        return Request(
            {
                "type": "http",
                "app": app,
                "method": "POST",
                "path": "/internal/service-trust",
                "headers": headers,
            }
        )

    clock = [NOW]
    store = TrustPolicyStore(
        tmp_path / "trust.sqlite", clock=lambda: clock[0], management_enabled=True, initialize=True
    )
    return store, request, clock


def publish(authority, data, revision=0, operation=None, token="manager"):
    store, request, _ = authority
    return store.publish(
        encoded(data), expected_revision=revision, operation_id=operation or uuid4(), request=request(token)
    )


def test_reference_pae_and_utf8_lengths():
    assert (
        pae("http://example.com/HelloWorld", b"hello world")
        == b"DSSEv1 29 http://example.com/HelloWorld 11 hello world"
    )
    assert pae("中", b"") == b"DSSEv1 3 \xe4\xb8\xad 0 "


@pytest.mark.parametrize("url,keyids", [(False, True), (True, True), (False, False), (True, False)])
def test_real_signatures_and_optional_hints_produce_bounded_evidence(authority, url, keyids):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    result = publish(authority, policy([key]))
    evidence = verify_service_signature(envelope(keys=[key], url=url, keyids=keyids), MANIFEST, store)
    assert result["revision"] == evidence.trust_revision == 1
    assert evidence.declaration_digest == digest(DECLARATION)
    assert evidence.signers == (digest(public(key)),)
    assert evidence.valid_until == NOW + 300
    assert evidence.capability_manifest_digest == digest(MANIFEST)
    assert not hasattr(evidence, "token") and not hasattr(evidence, "admission_granted")


@pytest.mark.parametrize(
    "kind", ["signature", "payload", "type", "manifest", "commit", "namespace", "policy", "artifact"]
)
def test_tampering_or_substitution_is_not_verified(authority, kind):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    raw, manifest = envelope(keys=[key]), MANIFEST
    data = json.loads(raw)
    if kind == "signature":
        signature = bytearray(base64.b64decode(data["signatures"][0]["sig"]))
        signature[0] ^= 1
        data["signatures"][0]["sig"] = base64.b64encode(signature).decode()
    elif kind == "payload":
        data["payload"] = base64.b64encode(DECLARATION + b" ").decode()
    elif kind == "type":
        raw = envelope(keys=[key], payload_type="application/json")
        data = json.loads(raw)
    elif kind == "manifest":
        manifest += b" "
    else:
        declaration = json.loads(DECLARATION)
        if kind == "commit":
            declaration["release_commit"] = "a" * 40
        elif kind == "namespace":
            declaration["plugin_id"] = "example.other"
        elif kind == "policy":
            declaration["execution_policy"]["digest"] = "sha256:" + "a" * 64
        else:
            declaration["artifact_digest"] = "sha256:" + "a" * 64
        data["payload"] = base64.b64encode(encoded(declaration)).decode()
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(encoded(data), manifest, store)


def test_self_asserted_roots_unknown_extensions_and_key_hints_cannot_grant_trust(authority):
    store, _, _ = authority
    legitimate, attacker = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    publish(authority, policy([legitimate]))
    data = json.loads(envelope(keys=[attacker]))
    data["trust_policy"] = policy([attacker])
    data["key_url"] = "https://attacker.invalid/key"
    data["signatures"][0]["public_key"] = key_record(attacker)["public_key"]
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(encoded(data), MANIFEST, store)
    data = json.loads(envelope(keys=[legitimate]))
    data["unknown"] = {"command": ["never", "execute"]}
    data["signatures"][0]["unknown"] = "ignored DSSE extension"
    assert verify_service_signature(encoded(data), MANIFEST, store).trust_revision == 1
    data["signatures"][0]["keyid"] = digest(public(attacker))
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(encoded(data), MANIFEST, store)


@pytest.mark.parametrize("owner", ["example.other", "example.service-child"])
def test_namespace_is_exact_not_inferred_from_a_valid_signature(authority, owner):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    data = policy([key])
    data["keys"][0]["plugin_ids"] = [owner]
    publish(authority, data)
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[key]), MANIFEST, store)


def test_threshold_counts_distinct_authorized_public_keys_and_ignores_unknown_signatures(authority):
    store, _, _ = authority
    one, two, unknown = (Ed25519PrivateKey.generate() for _ in range(3))
    publish(authority, policy([one, two], threshold=2))
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[one, one]), MANIFEST, store)
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[one, unknown]), MANIFEST, store)
    evidence = verify_service_signature(envelope(keys=[one, two, unknown]), MANIFEST, store)
    assert len(set(evidence.signers)) == 2 and digest(public(unknown)) not in evidence.signers
    data = policy([one, two], threshold=2)
    data["keys"][1]["plugin_ids"] = ["example.other"]
    publish(authority, data, 1)
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[one, two]), MANIFEST, store)


def test_revocations_survive_restart_omission_and_mutation_gate_off(authority):
    store, _, clock = authority
    old, new = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    publish(authority, policy([old]))
    raw = envelope(keys=[old])
    assert verify_service_signature(raw, MANIFEST, store).trust_revision == 1
    publish(authority, policy([new], revoked_keys=[digest(public(old))]), 1)
    reader = TrustPolicyStore(store.path, clock=lambda: clock[0])
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(raw, MANIFEST, reader)
    assert verify_service_signature(envelope(keys=[new]), MANIFEST, reader).trust_revision == 2
    with pytest.raises(APIError) as error:
        publish(authority, policy([old]), 2)
    assert error.value.code == "TRUST_KEY_REVOKED"
    artifact = json.loads(DECLARATION)["artifact_digest"]
    publish(authority, policy([new], revoked_artifacts=[artifact]), 2)
    publish(authority, policy([new]), 3)
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[new]), MANIFEST, reader)
    assert artifact in reader.snapshot().revoked_artifacts


@pytest.mark.parametrize("kind", ["policy-expiry", "policy-future", "key-expiry", "key-future", "floor"])
def test_time_and_revision_floors_fail_closed(authority, kind):
    store, _, clock = authority
    key = Ed25519PrivateKey.generate()
    data = policy([key])
    if kind == "key-expiry":
        data["keys"][0]["expires_at"] = NOW
    if kind == "key-future":
        data["keys"][0]["not_before"] = NOW + 1
    publish(authority, data)
    if kind == "policy-expiry":
        clock[0] = NOW + 300
    if kind == "policy-future":
        clock[0] = NOW - 20
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(
            envelope(keys=[key]), MANIFEST, store, minimum_revision=2 if kind == "floor" else 0
        )


@pytest.mark.parametrize("invalid", ["a", "a=b=", "YWJj\n", "$$$", 123])
def test_base64_errors_are_rejected(invalid):
    with pytest.raises(ServiceSignatureError):
        decode_base64(invalid, 32)


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": "2"},
        {"threshold": 0},
        {"threshold": True},
        {"threshold": 17},
        {"issued_at": NOW + 300},
        {"expires_at": NOW + 86401},
        {"private_key": "SYNTHETIC_PRIVATE_DETAIL"},
        {"keys": [{"algorithm": "rsa"}]},
        {"revoked_keys": ["bad"]},
        {"threshold": "1"},
    ],
)
def test_invalid_policy_never_becomes_current(authority, change):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    with pytest.raises(ServiceSignatureError):
        publish(authority, policy([key], **change))
    with pytest.raises(ServiceSignatureError):
        store.snapshot()


@pytest.mark.parametrize(
    "field,value",
    [
        ("public_key", "!" * 43),
        ("algorithm", "rsa"),
        ("plugin_ids", ["builtin.platform"]),
        ("plugin_ids", ["example.service", "example.service"]),
        ("not_before", NOW + 600),
        ("expires_at", False),
    ],
)
def test_invalid_key_records_are_rejected(authority, field, value):
    key = Ed25519PrivateKey.generate()
    data = policy([key])
    data["keys"][0][field] = value
    with pytest.raises(ServiceSignatureError):
        publish(authority, data)


def test_duplicate_keys_and_duplicate_json_are_rejected(authority):
    store, request, _ = authority
    key = Ed25519PrivateKey.generate()
    data = policy([key, key])
    with pytest.raises(ServiceSignatureError):
        publish(authority, data)
    with pytest.raises(ServiceSignatureError):
        store.publish(
            b'{"schema_version":"1","schema_version":"1"}',
            expected_revision=0,
            operation_id=uuid4(),
            request=request(),
        )
    publish(authority, policy([key]))
    raw = envelope(keys=[key])
    duplicate = b'{"payload":"ambiguous",' + raw[1:]
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(duplicate, MANIFEST, store)


@pytest.mark.parametrize(
    "token,status", [(None, 401), ("agent", 403), ("admin", 403), ("xek_synthetic", 403)]
)
def test_management_reuses_identity_and_dedicated_interactive_permission(authority, token, status):
    with pytest.raises(APIError) as error:
        publish(authority, {}, token=token)
    assert error.value.status == status


def test_rollout_gate_is_default_off_and_missing_store_is_not_created(authority, tmp_path):
    store, request, clock = authority
    reader = TrustPolicyStore(store.path, clock=lambda: clock[0])
    with pytest.raises(APIError) as error:
        reader.publish(b"{}", expected_revision=0, operation_id=uuid4(), request=request())
    assert error.value.status == 503
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ServiceSignatureError):
        TrustPolicyStore(missing).snapshot()
    assert not missing.exists()


def test_idempotency_returns_current_state_without_restoring_old_policy(authority):
    store, request, clock = authority
    key = Ed25519PrivateKey.generate()
    data = policy([key])
    op = uuid4()
    publish(authority, data, operation=op)
    clock[0] = NOW + 400
    publish(authority, policy([], issued_at=NOW + 390, expires_at=NOW + 700), 1)
    duplicate = store.publish(encoded(data), expected_revision=0, operation_id=op, request=request())
    assert duplicate == {"revision": 2, "applied_revision": 1, "duplicate": True}
    with pytest.raises(APIError) as error:
        publish(authority, policy([]), 0, op)
    assert error.value.code == "TRUST_OPERATION_CONFLICT"


def test_audit_failure_rolls_back_policy_revision_and_revocation(authority):
    store, _, _ = authority
    one, two = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    publish(authority, policy([one]))
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON service_trust_audit BEGIN SELECT RAISE(ABORT,'SYNTHETIC_PRIVATE_DB_DETAIL'); END"
        )
    with pytest.raises(ServiceSignatureError) as error:
        publish(authority, policy([two], revoked_keys=[digest(public(one))]), 1)
    assert "SYNTHETIC_PRIVATE" not in str(error.value)
    assert store.snapshot().revision == 1 and digest(public(one)) not in store.snapshot().revoked_keys
    assert verify_service_signature(envelope(keys=[one]), MANIFEST, store).trust_revision == 1


def test_revocation_during_crypto_does_not_return_stale_evidence(authority, monkeypatch):
    store, _, _ = authority
    one, two = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    publish(authority, policy([one]))
    original = signatures._valid_signature
    changed = []

    def verify(*args):
        result = original(*args)
        if result and not changed:
            changed.append(True)
            publish(authority, policy([two], revoked_keys=[digest(public(one))]), 1)
        return result

    monkeypatch.setattr(signatures, "_valid_signature", verify)
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[one]), MANIFEST, store)
    assert verify_service_signature(envelope(keys=[two]), MANIFEST, store).trust_revision == 2


def test_expiration_during_crypto_does_not_return_stale_evidence(authority, monkeypatch):
    store, _, clock = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    original = signatures._valid_signature

    def verify(*args):
        result = original(*args)
        clock[0] = NOW + 300
        return result

    monkeypatch.setattr(signatures, "_valid_signature", verify)
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[key]), MANIFEST, store)


def test_no_network_process_or_registry_mutation_and_no_token_persistence(authority, monkeypatch, capsys):
    import socket
    import subprocess

    from xeonolab.capabilities import load_registry

    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    before = copy.deepcopy(load_registry().all())

    def forbidden(*args, **kwargs):
        pytest.fail("signature verification attempted network/process IO")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert verify_service_signature(envelope(keys=[key]), MANIFEST, store).trust_revision == 1
    assert load_registry().all() == before
    with sqlite3.connect(store.path) as db:
        dump = "\n".join(db.iterdump())
    assert "Bearer " not in dump and "manager" not in dump
    assert not capsys.readouterr().out


@pytest.mark.parametrize(
    "bad", [b"{", b'{"x":NaN}', b'{"x":' + b"[" * 5000 + b"]" * 5000 + b"}", b"x" * (MAX_ENVELOPE_BYTES + 1)]
)
def test_malformed_envelopes_are_bounded_and_safe(authority, bad):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    with pytest.raises(ServiceSignatureError) as error:
        verify_service_signature(bad, MANIFEST, store)
    assert len(str(error.value)) < 100


def test_oversize_payload_and_policy_are_rejected(authority):
    store, request, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(b"x" * (MAX_DECLARATION_BYTES + 1), keys=[key]), MANIFEST, store)
    with pytest.raises(ServiceSignatureError):
        store.publish(
            b"x" * (MAX_POLICY_BYTES + 1), expected_revision=1, operation_id=uuid4(), request=request()
        )


def test_generated_public_policy_schema_is_checked_in():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (
        json.loads((root / "infra/plugins/service-signature-trust.schema.json").read_bytes())
        == trust_policy_schema()
    )


def test_empty_policy_is_an_explicit_denial_not_an_allow_default(authority):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([]))
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[key]), MANIFEST, store)


def test_two_authorities_cas_and_audit_are_atomic(authority):
    from concurrent.futures import ThreadPoolExecutor

    store, request, clock = authority
    one, two = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    publish(authority, policy([one]))
    other = TrustPolicyStore(store.path, clock=lambda: clock[0], management_enabled=True)

    def update(target):
        try:
            return target.publish(
                encoded(policy([two])), expected_revision=1, operation_id=uuid4(), request=request()
            )
        except APIError as error:
            return error.status

    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(update, (store, other)))
    assert sum(isinstance(item, dict) for item in results) == 1 and 409 in results
    assert other.snapshot().revision == 2
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT COUNT(*) FROM service_trust_audit").fetchone()[0] == 2


@pytest.mark.parametrize("damage", ["hash", "missing-policy", "bad-revocation", "missing-meta"])
def test_corruption_fails_closed_without_bootstrapping_old_state(authority, damage):
    store, _, clock = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    with sqlite3.connect(store.path) as db:
        if damage == "hash":
            db.execute("UPDATE service_trust_policies SET digest='bad'")
        elif damage == "missing-policy":
            db.execute("DELETE FROM service_trust_policies")
        elif damage == "bad-revocation":
            db.execute("INSERT INTO service_trust_revocations VALUES('bad','bad')")
        else:
            db.execute("DELETE FROM service_trust_meta")
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[key]), MANIFEST, store)
    if damage == "missing-meta":
        with pytest.raises(ServiceSignatureError):
            TrustPolicyStore(store.path, initialize=True, clock=lambda: clock[0])


def test_revocation_budget_failure_is_transactional(authority, monkeypatch):
    import xeonolab.service_plugin_trust as trust

    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    monkeypatch.setattr(trust, "MAX_REVOCATIONS", 1)
    revoked = ["sha256:" + "1" * 64, "sha256:" + "2" * 64]
    with pytest.raises(ServiceSignatureError):
        publish(authority, policy([key], revoked_artifacts=revoked), 1)
    assert store.snapshot().revision == 1 and not store.snapshot().revoked_artifacts


def test_cli_verifies_current_policy_but_never_returns_an_admission_grant(authority, tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path

    store, _, clock = authority
    clock[0] = now = int(time.time())
    key = Ed25519PrivateKey.generate()
    data = policy([key], issued_at=now - 10, expires_at=now + 300)
    data["keys"][0].update(not_before=now - 10, expires_at=now + 600)
    publish(authority, data)
    envfile, manifest = tmp_path / "envelope.json", tmp_path / "manifest.json"
    envfile.write_bytes(envelope(keys=[key]))
    manifest.write_bytes(MANIFEST)
    script = Path(__file__).resolve().parents[1] / "scripts/verify_service_signature.py"
    command = [sys.executable, str(script), str(envfile), str(manifest), "--trust-db", str(store.path)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["signature_verified"] is True and output["admission_granted"] is False
    assert output["trust_revision"] == 1 and "example.service" not in result.stdout
    envfile.write_bytes(b'{"private":"SYNTHETIC_PRIVATE_DETAIL"}')
    failed = subprocess.run(command, capture_output=True, text=True, timeout=15)
    assert failed.returncode == 1
    assert "SYNTHETIC_PRIVATE" not in failed.stdout + failed.stderr and "Traceback" not in failed.stderr


def test_read_snapshots_close_connections(authority, monkeypatch):
    import xeonolab.service_plugin_trust as trust

    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    original = trust.sqlite3.connect
    connections = []

    def connect(*args, **kwargs):
        result = original(*args, **kwargs)
        connections.append(result)
        return result

    monkeypatch.setattr(trust.sqlite3, "connect", connect)
    for _ in range(3):
        store.snapshot()
    assert len(connections) == 3
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError):
            connection.execute("SELECT 1")


@pytest.mark.parametrize("value", [False, float("nan"), float("inf"), -1, "private-detail"])
def test_invalid_clock_never_enables_verification(authority, value):
    store, _, clock = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    clock[0] = value
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(envelope(keys=[key]), MANIFEST, store)


def test_unavailable_clock_errors_are_sanitized(authority):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))

    def failed_clock():
        raise RuntimeError("SYNTHETIC_PRIVATE_CLOCK_DETAIL")

    store._clock = failed_clock
    with pytest.raises(ServiceSignatureError) as error:
        verify_service_signature(envelope(keys=[key]), MANIFEST, store)
    assert "SYNTHETIC_PRIVATE" not in str(error.value)


@pytest.mark.parametrize("size", [0, 63, 256])
def test_decoded_non_ed25519_signatures_do_not_invalidate_a_satisfied_threshold(authority, size):
    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    data = json.loads(envelope(keys=[key]))
    data["signatures"].append({"sig": base64.b64encode(b"x" * size).decode()})
    assert verify_service_signature(encoded(data), MANIFEST, store).trust_revision == 1
    data["signatures"] = data["signatures"][1:]
    with pytest.raises(ServiceSignatureError):
        verify_service_signature(encoded(data), MANIFEST, store)


def test_signature_binds_original_manifest_with_unresolved_foreign_dependency(authority):
    import hashlib

    store, _, _ = authority
    key = Ed25519PrivateKey.generate()
    publish(authority, policy([key]))
    manifest = json.loads(MANIFEST)
    manifest["capabilities"][0]["dependencies"] = ["other.service.provider@^1.0.0"]
    raw = encoded(manifest)
    declaration = json.loads(DECLARATION)
    declaration["capability_manifest_digest"] = "sha256:" + hashlib.sha256(raw).hexdigest()
    evidence = verify_service_signature(envelope(encoded(declaration), keys=[key]), raw, store)
    assert evidence.capability_manifest_digest == digest(raw)
    assert "other.service.provider@^1.0.0" in raw.decode()
