"""Real durable policy revisions and constraint resolution, never workload execution."""

import copy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from threading import Barrier
from uuid import uuid4

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from xeonolab import service_execution_policy as policies
from xeonolab.common import APIError, Identity, base_app
from xeonolab.service_execution_policy import (
    MAX_POLICY_BYTES,
    ExecutionPolicyError,
    ExecutionPolicyStore,
    _digest,
    execution_policy_schema,
    parse_execution_policy,
    resolve_execution_policy,
)

from .test_service_plugin_contract import DECLARATION, MANIFEST

NOW = 2_000_000_000
POLICY = {
    "schema_version": "1",
    "id": "lookup-policy",
    "plugin_id": "example.service",
    "issued_at": NOW - 10,
    "expires_at": NOW + 300,
    "isolation": {
        "profile": "nonprivileged-private-namespaces",
        "root_filesystem": "read-only",
        "network": "gateway-ingress-no-egress",
        "secrets": "none",
        "host_mounts": "none",
    },
    "limits": {
        "memory_bytes": 128 * 1024**2,
        "cpu_millis": 500,
        "pids": 16,
        "scratch_bytes": 1024**2,
        "concurrency": 2,
        "queue": 0,
        "input_bytes": 8192,
        "output_bytes": 16384,
        "startup_seconds": 10,
        "invocation_seconds": 10,
        "drain_seconds": 5,
    },
    "capabilities": [
        {
            "id": "example.service.lookup",
            "kind": "tool",
            "version": "1.0.0",
            "minimum_permissions": ["xelibrary:read"],
            "allowed_permissions": ["xelibrary:read", "xelibrary:query"],
            "timeout_seconds": 5,
        }
    ],
}


def encoded(value):
    return json.dumps(value).encode()


def declaration(raw_policy, manifest=MANIFEST):
    result = json.loads(DECLARATION)
    result["execution_policy"] = {"id": "lookup-policy", "digest": _digest(raw_policy)}
    result["capability_manifest_digest"] = _digest(manifest)
    return encoded(result)


@pytest.fixture
def authority(settings, tmp_path):
    settings.rate_limit_enabled = False
    app = base_app("policy-test", settings)
    actors = {
        "human": Identity("operator", {"plugin-service:manage"}, "human"),
        "agent": Identity("codex", {"plugin-service:manage"}, "codex"),
        "admin": Identity("admin", {"desktop:admin", "deploy:approve"}, "human"),
        "xek_synthetic": Identity("operator", {"plugin-service:manage"}, "human"),
    }

    def resolver(token):
        if token not in actors:
            raise APIError(401, "UNAUTHENTICATED", "invalid identity")
        return actors[token]

    app.state.authorization_resolver = resolver

    def request(token="human"):
        headers = [(b"authorization", ("Bearer " + token).encode())] if token else []
        return Request(
            {
                "type": "http",
                "app": app,
                "method": "POST",
                "path": "/internal/service-policy",
                "headers": headers,
            }
        )

    clock = [NOW]
    store = ExecutionPolicyStore(
        tmp_path / "policies.sqlite",
        clock=lambda: clock[0],
        management_enabled=True,
        initialize=True,
    )
    return store, request, clock


def publish(authority, data=POLICY, revision=0, operation=None, token="human"):
    store, request, _ = authority
    return store.publish(
        encoded(data), expected_revision=revision, operation_id=operation or uuid4(), request=request(token)
    )


def test_durable_exact_binding_and_immutable_constraints(authority):
    store, _, _ = authority
    publish(authority)
    raw = encoded(POLICY)
    other = ExecutionPolicyStore(store.path, clock=lambda: NOW)
    result = resolve_execution_policy(declaration(raw), MANIFEST, other, minimum_revision=1)
    assert result.policy_digest == _digest(raw)
    assert result.declaration_digest == _digest(declaration(raw))
    assert result.capability_manifest_digest == _digest(MANIFEST)
    assert result.policy_revision == 1 and result.valid_until == NOW + 300
    assert result.policy.capabilities[0].minimum_permissions == frozenset({"xelibrary:read"})
    assert not hasattr(result, "admission_granted") and not hasattr(result, "token")
    with pytest.raises(FrozenInstanceError):
        result.policy.plugin_id = "different.plugin"
    with pytest.raises(ValidationError):
        result.policy.limits.concurrency = 100


@pytest.mark.parametrize(
    "path,value",
    [
        (("schema_version",), "2"),
        (("plugin_id",), "builtin.platform"),
        (("id",), "lookup-policy\n"),
        (("plugin_id",), "example.service\n"),
        (("expires_at",), NOW + 86401),
        (("issued_at",), NOW + 301),
        (("isolation", "network"), "all"),
        (("isolation", "root_filesystem"), "read-write"),
        (("isolation", "secrets"), "provider-key"),
        (("isolation", "host_mounts"), "/etc"),
        (("limits", "concurrency"), True),
        (("limits", "queue"), -1),
        (("limits", "cpu_millis"), 0),
        (("limits", "memory_bytes"), 0),
        (("limits", "pids"), 1025),
        (("limits", "input_bytes"), 16 * 1024**2 + 1),
        (("limits", "output_bytes"), 0),
        (("limits", "scratch_bytes"), 1024**3 + 1),
        (("limits", "startup_seconds"), 301),
        (("limits", "drain_seconds"), -1),
        (("capabilities", 0, "id"), "foreign.plugin.lookup"),
        (("capabilities", 0, "kind"), "app"),
        (("capabilities", 0, "version"), "1.0.0\n"),
        (("capabilities", 0, "version"), "1.0.0-rc.1"),
        (("capabilities", 0, "version"), "01.0.0"),
        (("capabilities", 0, "timeout_seconds"), 11),
        (("capabilities", 0, "allowed_permissions"), ["deploy:execute", "xelibrary:read"]),
        (("capabilities", 0, "allowed_permissions"), ["accounts:manage", "xelibrary:read"]),
        (("capabilities", 0, "allowed_permissions"), ["plugin-service:manage", "xelibrary:read"]),
        (("capabilities", 0, "allowed_permissions"), []),
        (("capabilities", 0, "minimum_permissions"), ["xelibrary:read", "xelibrary:read"]),
        (("capabilities", 0, "allowed_permissions"), ["xelibrary:read", "xelibrary:read"]),
    ],
)
def test_strict_policy_rejects_broadening_and_malformed_inputs(path, value):
    data = copy.deepcopy(POLICY)
    target = data
    for field in path[:-1]:
        target = target[field]
    target[path[-1]] = value
    with pytest.raises(ExecutionPolicyError, match="^invalid execution policy$"):
        parse_execution_policy(encoded(data))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"secret": "do-not-echo", "secret": "duplicate"}',
        b"[" * 2000 + b"]" * 2000,
        b"NaN",
        bytes([255]),
        b" " * (MAX_POLICY_BYTES + 1),
        b"{}",
    ],
)
def test_bad_json_is_bounded_and_errors_fixed(raw):
    with pytest.raises(ExecutionPolicyError) as error:
        parse_execution_policy(raw)
    assert str(error.value) == "invalid execution policy"


@pytest.mark.parametrize(
    "mutation", ["floor", "ceiling", "timeout", "kind", "version", "capability", "digest", "plugin"]
)
def test_resolution_rejects_substitution_and_weakening(authority, mutation):
    store, _, _ = authority
    publish(authority)
    raw = encoded(POLICY)
    manifest = json.loads(MANIFEST)
    cap = manifest["capabilities"][0]
    if mutation == "floor":
        cap["permissions"] = []
    if mutation == "ceiling":
        cap["permissions"] += ["xelibrary:write"]
    if mutation == "timeout":
        cap["timeout_seconds"] = 6
    if mutation == "kind":
        cap["kind"] = "worker"
    if mutation == "version":
        cap["version"] = "1.0.1"
    if mutation == "capability":
        cap["id"] = "example.service.different"
    manifest_bytes = encoded(manifest)
    decl = json.loads(declaration(raw, manifest_bytes))
    if mutation == "digest":
        decl["execution_policy"]["digest"] = "sha256:" + "0" * 64
    if mutation == "plugin":
        # Use a valid but differently owned manifest/declaration.
        manifest["id"] = "different.service"
        cap["id"] = "different.service.lookup"
        manifest_bytes = encoded(manifest)
        decl["plugin_id"] = "different.service"
        decl["capability_manifest_digest"] = _digest(manifest_bytes)
    with pytest.raises(ExecutionPolicyError, match="^service execution policy resolution failed$"):
        resolve_execution_policy(encoded(decl), manifest_bytes, store)


def test_disable_survives_restart_and_closed_management_gate(authority):
    store, request, _ = authority
    publish(authority)
    store.disable("lookup-policy", expected_revision=1, operation_id=uuid4(), request=request())
    reopened = ExecutionPolicyStore(store.path, clock=lambda: NOW)
    with pytest.raises(ExecutionPolicyError):
        resolve_execution_policy(declaration(encoded(POLICY)), MANIFEST, reopened)
    with pytest.raises(APIError) as error:
        reopened.publish(encoded(POLICY), expected_revision=2, operation_id=uuid4(), request=request())
    assert error.value.status == 503


def test_idempotence_conflicts_and_current_revision(authority):
    store, request, _ = authority
    op = uuid4()
    assert publish(authority, operation=op)["revision"] == 1
    assert publish(authority, operation=op) == {"revision": 1, "applied_revision": 1, "duplicate": True}
    disable_op = uuid4()
    store.disable("lookup-policy", expected_revision=1, operation_id=disable_op, request=request())
    assert publish(authority, operation=op) == {"revision": 2, "applied_revision": 1, "duplicate": True}
    with pytest.raises(ExecutionPolicyError):
        store.snapshot("lookup-policy")
    for action in (
        lambda: publish(authority, {**POLICY, "expires_at": NOW + 200}, operation=op),
        lambda: publish(authority, revision=1, operation=op),
        lambda: store.disable("lookup-policy", expected_revision=0, operation_id=op, request=request()),
        lambda: publish(authority, revision=0),
    ):
        with pytest.raises(APIError) as error:
            action()
        assert error.value.status == 409


@pytest.mark.parametrize(
    "token,status", [(None, 401), ("agent", 403), ("admin", 403), ("xek_synthetic", 403)]
)
def test_management_requires_separate_interactive_authority(authority, token, status):
    with pytest.raises(APIError) as error:
        publish(authority, token=token)
    assert error.value.status == status


def test_expiry_clock_and_revision_floor_fail_closed(authority):
    store, _, clock = authority
    publish(authority)
    with pytest.raises(ExecutionPolicyError):
        store.snapshot("lookup-policy", minimum_revision=2)
    for now in (NOW + 300, NOW - 11, float("nan"), True, -1):
        clock[0] = now
        with pytest.raises(ExecutionPolicyError):
            store.snapshot("lookup-policy")


def test_two_writers_use_one_global_cas(authority):
    store, request, _ = authority
    publish(authority)
    barrier = Barrier(2)

    def disable():
        local = ExecutionPolicyStore(store.path, clock=lambda: NOW, management_enabled=True)
        barrier.wait()
        try:
            return local.disable(
                "lookup-policy", expected_revision=1, operation_id=uuid4(), request=request()
            )["revision"]
        except APIError as error:
            return error.status

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: disable(), range(2)))
    assert sorted(results) == [2, 409]


def test_audit_failure_rolls_back_all_state(authority):
    store, _, _ = authority
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON service_policy_audit BEGIN SELECT RAISE(ABORT, 'secret'); END"
        )
    with pytest.raises(ExecutionPolicyError, match="^execution policy write unavailable$"):
        publish(authority)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT revision FROM service_policy_meta").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM service_policy_current").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM service_policy_history").fetchone()[0] == 0


def test_concurrent_disable_and_expiry_during_resolution_deny(authority, monkeypatch):
    store, request, clock = authority
    publish(authority)
    real = store.snapshot
    calls = []

    def snapshot(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            store.disable("lookup-policy", expected_revision=1, operation_id=uuid4(), request=request())
        return result

    monkeypatch.setattr(store, "snapshot", snapshot)
    with pytest.raises(ExecutionPolicyError):
        resolve_execution_policy(declaration(encoded(POLICY)), MANIFEST, store)
    publish(authority, revision=2)
    calls.clear()

    def expiring_snapshot(*args, **kwargs):
        result = real(*args, **kwargs)
        clock[0] = NOW + 300
        return result

    monkeypatch.setattr(store, "snapshot", expiring_snapshot)
    with pytest.raises(ExecutionPolicyError):
        resolve_execution_policy(declaration(encoded(POLICY)), MANIFEST, store)


def test_missing_corrupt_and_deleted_metadata_cannot_initialize_trust(authority, tmp_path):
    store, _, _ = authority
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(ExecutionPolicyError):
        ExecutionPolicyStore(missing).snapshot("lookup-policy")
    assert not missing.exists()
    publish(authority)
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE service_policy_history SET body=?", (b"secret malformed policy",))
    with pytest.raises(ExecutionPolicyError):
        store.snapshot("lookup-policy")
    with sqlite3.connect(store.path) as db:
        db.execute("DELETE FROM service_policy_meta")
    with pytest.raises(ExecutionPolicyError):
        ExecutionPolicyStore(store.path, initialize=True)


def test_policy_resolution_has_no_network_process_registration_or_token_side_effect(authority, monkeypatch):
    import socket
    import subprocess
    import urllib.request

    from xeonolab.capabilities import load_registry

    store, _, _ = authority
    before = load_registry().all()

    def forbidden(*args, **kwargs):
        raise AssertionError("side effect forbidden")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    publish(authority)
    result = resolve_execution_policy(declaration(encoded(POLICY)), MANIFEST, store)
    assert load_registry().all() == before
    assert "Bearer" not in repr(result) and "human" not in repr(result)
    assert b"Bearer" not in store.path.read_bytes()


def test_generated_schema_does_not_drift():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    assert (
        json.loads((root / "infra/plugins/service-execution-policy.schema.json").read_text())
        == execution_policy_schema()
    )


def test_read_only_snapshots_close_connections_even_on_denial(authority, monkeypatch):
    store, _, clock = authority
    publish(authority)
    real_connect = sqlite3.connect
    closed = []

    class ObservedConnection(sqlite3.Connection):
        def close(self):
            closed.append(True)
            return super().close()

    def connect(*args, **kwargs):
        assert kwargs.get("uri") is True
        assert args[0].endswith("?mode=ro")
        return real_connect(*args, **kwargs, factory=ObservedConnection)

    monkeypatch.setattr(policies.sqlite3, "connect", connect)
    store.snapshot("lookup-policy")
    clock[0] = NOW + 300
    with pytest.raises(ExecutionPolicyError):
        store.snapshot("lookup-policy")
    assert len(closed) == 2


def test_replacement_and_unrelated_policy_revision_changes_invalidate_evidence(authority, monkeypatch):
    store, _, _ = authority
    publish(authority)
    real = store.snapshot
    first = [True]

    def replacing_snapshot(*args, **kwargs):
        result = real(*args, **kwargs)
        if first[0]:
            first[0] = False
            publish(authority, {**POLICY, "id": "other-policy"}, revision=1)
        return result

    monkeypatch.setattr(store, "snapshot", replacing_snapshot)
    with pytest.raises(ExecutionPolicyError):
        resolve_execution_policy(declaration(encoded(POLICY)), MANIFEST, store)
    # A second snapshot with the same exact policy bytes and current global
    # revision can be resolved; no stale revision is silently treated current.
    assert resolve_execution_policy(declaration(encoded(POLICY)), MANIFEST, store).policy_revision == 2
    publish(authority, {**POLICY, "expires_at": NOW + 200}, revision=2)
    with pytest.raises(ExecutionPolicyError):
        resolve_execution_policy(declaration(encoded(POLICY)), MANIFEST, store)


@pytest.mark.parametrize("value", [True, -1, 2**63, "1"])
def test_invalid_revision_never_mutates(authority, value):
    with pytest.raises(APIError) as error:
        publish(authority, revision=value)
    assert error.value.status == 422
    with pytest.raises(ExecutionPolicyError):
        authority[0].snapshot("lookup-policy", minimum_revision=value)


def test_bad_operation_missing_policy_and_default_gate(authority):
    store, request, _ = authority
    with pytest.raises(APIError) as error:
        store.publish(encoded(POLICY), expected_revision=0, operation_id="unsafe-secret", request=request())
    assert error.value.status == 422 and "unsafe-secret" not in str(error.value.message)
    with pytest.raises(APIError) as error:
        store.disable("lookup-policy", expected_revision=0, operation_id=uuid4(), request=request())
    assert error.value.status == 404
    closed = ExecutionPolicyStore(store.path)
    with pytest.raises(APIError) as error:
        closed.publish(encoded(POLICY), expected_revision=0, operation_id=uuid4(), request=request())
    assert error.value.status == 503


def test_clock_unavailable_rejects_changes_and_reads(authority):
    store, _, _ = authority

    def bad_clock():
        raise RuntimeError("do-not-echo-clock-secret")

    store._clock = bad_clock
    with pytest.raises(ExecutionPolicyError, match="^execution policy clock unavailable$"):
        publish(authority)


def test_root_capability_id_and_exact_empty_permission_contract(authority):
    policy = copy.deepcopy(POLICY)
    policy["capabilities"][0].update(id="example.service", minimum_permissions=[], allowed_permissions=[])
    manifest = json.loads(MANIFEST)
    manifest["capabilities"][0].update(id="example.service", permissions=[])
    publish(authority, policy)
    resolve_execution_policy(declaration(encoded(policy), encoded(manifest)), encoded(manifest), authority[0])


def test_unknown_fields_duplicate_rules_and_scope_whitespace_rejected():
    candidates = []
    for path in ((), ("isolation",), ("limits",), ("capabilities", 0)):
        data = copy.deepcopy(POLICY)
        target = data
        for component in path:
            target = target[component]
        target["publisher_command"] = "do-not-run"
        candidates.append(data)
    data = copy.deepcopy(POLICY)
    data["capabilities"] *= 2
    candidates.append(data)
    data = copy.deepcopy(POLICY)
    data["capabilities"][0]["allowed_permissions"].append("xelibrary:read" + chr(10))
    candidates.append(data)
    for data in candidates:
        with pytest.raises(ExecutionPolicyError):
            parse_execution_policy(encoded(data))


def test_cli_only_reads_existing_authority_and_reports_no_admission(authority, tmp_path):
    import subprocess
    import sys
    import time
    from pathlib import Path

    store, request, clock = authority
    clock[0] = int(time.time())
    policy = {**POLICY, "issued_at": clock[0] - 10, "expires_at": clock[0] + 300}
    publish(authority, policy)
    decl = tmp_path / "declaration.json"
    manifest = tmp_path / "manifest.json"
    decl.write_bytes(declaration(encoded(policy)))
    manifest.write_bytes(MANIFEST)
    root = Path(__file__).resolve().parents[1]
    cmd = [
        sys.executable,
        str(root / "scripts/resolve_service_policy.py"),
        str(decl),
        str(manifest),
        "--policy-db",
        str(store.path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert output["policy_resolved"] is True and output["admission_granted"] is False
    assert output["policy_digest"] == _digest(encoded(policy))
    store.disable("lookup-policy", expected_revision=1, operation_id=uuid4(), request=request())
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
    assert result.returncode == 1
    assert not result.stdout and result.stderr == "Service execution policy could not be resolved." + chr(10)
    assert str(store.path) not in result.stderr


@pytest.mark.parametrize(
    "permission",
    [
        "desktop:admin",
        "dev:write",
        "capabilities:manage",
        "plugin:manage",
        "plugins:manage",
        "network:manage",
        "unknown:manage",
    ],
)
def test_first_profile_cannot_request_control_or_unknown_permission_domains(permission):
    data = copy.deepcopy(POLICY)
    data["capabilities"][0]["allowed_permissions"].append(permission)
    with pytest.raises(ExecutionPolicyError):
        parse_execution_policy(encoded(data))


@pytest.mark.parametrize("field", ["minimum_permissions", "allowed_permissions"])
def test_permission_strings_are_bounded_in_model_and_schema(field):
    data = copy.deepcopy(POLICY)
    data["capabilities"][0][field] = ["xelibrary:" + "a" * 129]
    with pytest.raises(ExecutionPolicyError):
        parse_execution_policy(encoded(data))
    rule_schema = execution_policy_schema()["$defs"]["_RuleInput"]["properties"][field]["items"]
    assert rule_schema["maxLength"] == 128


def test_disabled_policy_write_audit_failure_preserves_enabled_state(authority):
    store, request, _ = authority
    publish(authority)
    with sqlite3.connect(store.path) as db:
        db.execute(
            "CREATE TRIGGER fail_disable BEFORE INSERT ON service_policy_audit "
            "WHEN NEW.action='disable' BEGIN SELECT RAISE(ABORT,'secret'); END"
        )
    with pytest.raises(ExecutionPolicyError, match="^execution policy write unavailable$"):
        store.disable("lookup-policy", expected_revision=1, operation_id=uuid4(), request=request())
    assert store.snapshot("lookup-policy").revision == 1


def test_disabled_policy_can_only_be_restored_by_a_new_explicit_human_publication(authority):
    store, request, _ = authority
    publish(authority)
    op = uuid4()
    store.disable("lookup-policy", expected_revision=1, operation_id=op, request=request())
    assert store.disable("lookup-policy", expected_revision=1, operation_id=op, request=request())[
        "duplicate"
    ]
    publish(authority, revision=2)
    assert store.snapshot("lookup-policy").revision == 3
    # Retrying an earlier disable acknowledges history; it does not lie about
    # the current state or silently disable the newly reviewed revision.
    result = store.disable("lookup-policy", expected_revision=1, operation_id=op, request=request())
    assert result == {"revision": 3, "applied_revision": 2, "duplicate": True}
    assert store.snapshot("lookup-policy").revision == 3
