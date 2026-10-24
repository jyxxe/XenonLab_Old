"""Core-owned service execution constraints. Resolution never authorizes execution."""

import hashlib
import math
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .capabilities import CAPABILITY_ID, PERMISSION, PLUGIN_ID
from .common import APIError, identity, transaction
from .service_plugin_contract import ServicePluginContractError, _load, validate_service_plugin

MAX_POLICY_BYTES = 64 * 1024
MAX_REVISION = 2**63 - 1
MAX_LIFETIME = 86400
STABLE_VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
SERVICE_PERMISSION_DOMAINS = frozenset({"xelibrary", "embedding", "llm", "lean"})
PermissionName = Annotated[
    str,
    Field(
        pattern="^(" + "|".join(sorted(SERVICE_PERMISSION_DOMAINS)) + "):[a-z0-9-]+$",
        min_length=3,
        max_length=128,
        json_schema_extra={"not": {"pattern": r"\s"}},
    ),
]
PolicyId = Annotated[
    str,
    Field(
        pattern=r"^[a-z][a-z0-9-]{1,63}$",
        min_length=2,
        max_length=64,
        json_schema_extra={"not": {"pattern": r"\s"}},
    ),
]


class ExecutionPolicyError(ValueError):
    """Fixed public diagnostics contain neither publisher values nor DB details."""


def _digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ResourceLimits(_Input):
    # Protocol bounds are validation ceilings, not production allocations.
    memory_bytes: int = Field(ge=16 * 1024**2, le=64 * 1024**3)
    cpu_millis: int = Field(ge=1, le=64000)
    pids: int = Field(ge=1, le=1024)
    scratch_bytes: int = Field(ge=0, le=1024**3)
    concurrency: int = Field(ge=1, le=128)
    queue: int = Field(ge=0, le=1024)
    input_bytes: int = Field(ge=1, le=16 * 1024**2)
    output_bytes: int = Field(ge=1, le=16 * 1024**2)
    startup_seconds: int = Field(ge=1, le=300)
    invocation_seconds: int = Field(ge=1, le=300)
    drain_seconds: int = Field(ge=0, le=300)


class IsolationRequirements(_Input):
    profile: Literal["nonprivileged-private-namespaces"]
    root_filesystem: Literal["read-only"]
    network: Literal["gateway-ingress-no-egress"]
    secrets: Literal["none"]
    host_mounts: Literal["none"]


class _RuleInput(_Input):
    id: str = Field(
        pattern=CAPABILITY_ID.pattern,
        min_length=3,
        max_length=128,
        json_schema_extra={"not": {"pattern": r"\s"}},
    )
    kind: Literal["tool", "agent", "provider", "worker"]
    version: str = Field(
        pattern="^" + STABLE_VERSION + "$",
        min_length=5,
        max_length=256,
        json_schema_extra={"not": {"pattern": r"\s"}},
    )
    minimum_permissions: list[PermissionName] = Field(max_length=32, json_schema_extra={"uniqueItems": True})
    allowed_permissions: list[PermissionName] = Field(max_length=32, json_schema_extra={"uniqueItems": True})
    timeout_seconds: int = Field(ge=1, le=300)


class _PolicyInput(_Input):
    schema_version: Literal["1"]
    id: PolicyId
    plugin_id: str = Field(
        pattern=PLUGIN_ID.pattern, min_length=2, max_length=63, json_schema_extra={"not": {"pattern": r"\s"}}
    )
    issued_at: int = Field(ge=0, le=MAX_REVISION)
    expires_at: int = Field(gt=0, le=MAX_REVISION)
    isolation: IsolationRequirements
    limits: ResourceLimits
    capabilities: list[_RuleInput] = Field(min_length=1, max_length=64)


@dataclass(frozen=True)
class CapabilityPolicy:
    id: str
    kind: str
    version: str
    minimum_permissions: frozenset[str]
    allowed_permissions: frozenset[str]
    timeout_seconds: int


@dataclass(frozen=True)
class ExecutionPolicy:
    id: str
    plugin_id: str
    issued_at: int
    expires_at: int
    isolation: IsolationRequirements
    limits: ResourceLimits
    capabilities: tuple[CapabilityPolicy, ...]


def parse_execution_policy(raw):
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_POLICY_BYTES:
            raise ValueError
        raw.decode("utf-8")
        data = _PolicyInput.model_validate(_load(raw, MAX_POLICY_BYTES))
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", data.id):
            raise ValueError
        if (
            not PLUGIN_ID.fullmatch(data.plugin_id)
            or data.plugin_id == "builtin"
            or data.plugin_id.startswith("builtin.")
        ):
            raise ValueError
        if not 0 < data.expires_at - data.issued_at <= MAX_LIFETIME:
            raise ValueError
        rules, seen = [], set()
        for entry in data.capabilities:
            if not CAPABILITY_ID.fullmatch(entry.id) or not (
                entry.id == data.plugin_id or entry.id.startswith(data.plugin_id + ".")
            ):
                raise ValueError
            if (
                entry.id in seen
                or entry.timeout_seconds > data.limits.invocation_seconds
                or not re.fullmatch(STABLE_VERSION, entry.version)
            ):
                raise ValueError
            floor, ceiling = frozenset(entry.minimum_permissions), frozenset(entry.allowed_permissions)
            if len(floor) != len(entry.minimum_permissions) or len(ceiling) != len(entry.allowed_permissions):
                raise ValueError
            if not floor <= ceiling or any(
                not PERMISSION.fullmatch(p) or p.split(":", 1)[0] not in SERVICE_PERMISSION_DOMAINS
                for p in ceiling
            ):
                raise ValueError
            seen.add(entry.id)
            rules.append(
                CapabilityPolicy(entry.id, entry.kind, entry.version, floor, ceiling, entry.timeout_seconds)
            )
        return ExecutionPolicy(
            data.id,
            data.plugin_id,
            data.issued_at,
            data.expires_at,
            data.isolation,
            data.limits,
            tuple(rules),
        )
    except (ValueError, UnicodeError, TypeError, ValidationError, ServicePluginContractError):
        raise ExecutionPolicyError("invalid execution policy") from None


@dataclass(frozen=True)
class PolicySnapshot:
    revision: int
    policy_digest: str
    policy: ExecutionPolicy
    checked_at: int


def _operation(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise APIError(422, "INVALID_POLICY_OPERATION", "UUID operation id required") from None


class ExecutionPolicyStore:
    """Explicit core path only. No startup hook, publisher path selection or HTTP API."""

    def __init__(self, path, *, clock=time.time, management_enabled=False, initialize=False):
        if type(management_enabled) is not bool or type(initialize) is not bool:
            raise ValueError("execution policy gates must be booleans")
        self.path, self._clock, self.management_enabled = Path(path), clock, management_enabled
        if initialize:
            with transaction(self.path) as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS service_policy_meta(
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS service_policy_history(
                        revision INTEGER PRIMARY KEY, policy_id TEXT NOT NULL,
                        digest TEXT NOT NULL, body BLOB NOT NULL);
                    CREATE TABLE IF NOT EXISTS service_policy_current(
                        policy_id TEXT PRIMARY KEY, revision INTEGER NOT NULL,
                        digest TEXT NOT NULL, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)));
                    CREATE TABLE IF NOT EXISTS service_policy_audit(
                        actor TEXT NOT NULL, operation_id TEXT NOT NULL, action TEXT NOT NULL,
                        policy_id TEXT NOT NULL, digest TEXT NOT NULL, expected_revision INTEGER NOT NULL,
                        applied_revision INTEGER NOT NULL, at INTEGER NOT NULL,
                        PRIMARY KEY(actor,operation_id));
                """)
                if (
                    db.execute("SELECT revision FROM service_policy_meta WHERE singleton=1").fetchone()
                    is None
                ):
                    if any(
                        db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                        for table in (
                            "service_policy_history",
                            "service_policy_current",
                            "service_policy_audit",
                        )
                    ):
                        raise ExecutionPolicyError("execution policy state unavailable")
                    db.execute("INSERT INTO service_policy_meta VALUES(1,0)")

    def now(self):
        try:
            value = self._clock()
        except Exception:
            raise ExecutionPolicyError("execution policy clock unavailable") from None
        if type(value) not in (int, float) or not 0 <= value <= MAX_REVISION or not math.isfinite(value):
            raise ExecutionPolicyError("execution policy clock unavailable")
        return int(value)

    @staticmethod
    def _revision(db):
        row = db.execute("SELECT revision FROM service_policy_meta WHERE singleton=1").fetchone()
        if row is None or type(row[0]) is not int or not 0 <= row[0] <= MAX_REVISION:
            raise ExecutionPolicyError("execution policy state unavailable")
        return row[0]

    def snapshot(self, policy_id, *, minimum_revision=0):
        if (
            not isinstance(policy_id, str)
            or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", policy_id)
            or type(minimum_revision) is not int
            or not 0 <= minimum_revision <= MAX_REVISION
        ):
            raise ExecutionPolicyError("invalid execution policy reference")
        try:
            with closing(
                sqlite3.connect(self.path.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as db:
                db.execute("BEGIN")
                revision = self._revision(db)
                if revision < minimum_revision:
                    raise ExecutionPolicyError("execution policy revision unavailable")
                row = db.execute(
                    "SELECT c.revision,c.digest,c.enabled,h.policy_id,h.digest,h.body "
                    "FROM service_policy_current c JOIN service_policy_history h ON h.revision=c.revision "
                    "WHERE c.policy_id=?",
                    (policy_id,),
                ).fetchone()
                if (
                    row is None
                    or type(row[0]) is not int
                    or not 1 <= row[0] <= revision
                    or type(row[2]) is not int
                    or row[2] != 1
                    or row[3] != policy_id
                    or row[1] != row[4]
                    or not isinstance(row[5], bytes)
                    or _digest(row[5]) != row[1]
                ):
                    raise ExecutionPolicyError("execution policy unavailable or disabled")
                policy = parse_execution_policy(row[5])
                now = self.now()
                if policy.id != policy_id or not policy.issued_at <= now < policy.expires_at:
                    raise ExecutionPolicyError("execution policy expired or not active")
                return PolicySnapshot(revision, row[1], policy, now)
        except (sqlite3.Error, OSError):
            raise ExecutionPolicyError("execution policy state unavailable") from None

    def publish(self, raw, *, expected_revision, operation_id, request):
        return self._mutate("publish", raw, None, expected_revision, operation_id, request)

    def disable(self, policy_id, *, expected_revision, operation_id, request):
        return self._mutate("disable", None, policy_id, expected_revision, operation_id, request)

    def _mutate(self, action, raw, policy_id, expected_revision, operation_id, request):
        actor = identity(request)
        actor.require("plugin-service:manage")
        if actor.kind != "human" or request.headers.get("Authorization", "").startswith("Bearer xek_"):
            raise APIError(403, "INTERACTIVE_LOGIN_REQUIRED", "human OIDC login required for policy changes")
        if not self.management_enabled:
            raise APIError(503, "SERVICE_POLICY_NOT_ENABLED", "execution policy management is not enabled")
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION:
            raise APIError(422, "INVALID_POLICY_REVISION", "invalid expected policy revision")
        operation = _operation(operation_id)
        policy = parse_execution_policy(raw) if action == "publish" else None
        policy_id = policy.id if policy else policy_id
        if not isinstance(policy_id, str) or not re.fullmatch(r"[a-z][a-z0-9-]{1,63}", policy_id):
            raise ExecutionPolicyError("invalid execution policy reference")
        body_digest = _digest(raw) if policy else ""
        try:
            with transaction(self.path) as db:
                revision = self._revision(db)
                old = db.execute(
                    "SELECT * FROM service_policy_audit WHERE actor=? AND operation_id=?",
                    (actor.sub, operation),
                ).fetchone()
                if old:
                    if (old["action"], old["policy_id"], old["digest"], old["expected_revision"]) != (
                        action,
                        policy_id,
                        body_digest,
                        expected_revision,
                    ):
                        raise APIError(
                            409, "POLICY_OPERATION_CONFLICT", "operation id reused with different input"
                        )
                    return {
                        "revision": revision,
                        "applied_revision": old["applied_revision"],
                        "duplicate": True,
                    }
                if revision != expected_revision:
                    raise APIError(409, "POLICY_REVISION_CONFLICT", "execution policy revision changed")
                if revision == MAX_REVISION:
                    raise APIError(409, "POLICY_REVISION_EXHAUSTED", "execution policy revision exhausted")
                now = self.now()
                if policy and not policy.issued_at <= now < policy.expires_at:
                    raise ExecutionPolicyError("execution policy expired or not active")
                if (
                    not policy
                    and db.execute(
                        "SELECT 1 FROM service_policy_current WHERE policy_id=?",
                        (policy_id,),
                    ).fetchone()
                    is None
                ):
                    raise APIError(404, "EXECUTION_POLICY_NOT_FOUND", "execution policy not found")
                revision += 1
                if policy:
                    db.execute(
                        "INSERT INTO service_policy_history VALUES(?,?,?,?)",
                        (revision, policy_id, body_digest, raw),
                    )
                    db.execute(
                        "INSERT INTO service_policy_current VALUES(?,?,?,1) ON CONFLICT(policy_id) "
                        "DO UPDATE SET revision=excluded.revision,digest=excluded.digest,enabled=1",
                        (policy_id, revision, body_digest),
                    )
                else:
                    db.execute("UPDATE service_policy_current SET enabled=0 WHERE policy_id=?", (policy_id,))
                db.execute("UPDATE service_policy_meta SET revision=? WHERE singleton=1", (revision,))
                db.execute(
                    "INSERT INTO service_policy_audit VALUES(?,?,?,?,?,?,?,?)",
                    (actor.sub, operation, action, policy_id, body_digest, expected_revision, revision, now),
                )
                return {"revision": revision, "applied_revision": revision, "duplicate": False}
        except (sqlite3.Error, OSError):
            raise ExecutionPolicyError("execution policy write unavailable") from None


@dataclass(frozen=True)
class ResolvedExecutionPolicy:
    declaration_digest: str
    capability_manifest_digest: str
    policy_digest: str
    policy_revision: int
    policy: ExecutionPolicy
    checked_at: int
    valid_until: int


def resolve_execution_policy(declaration_bytes, manifest_bytes, store, *, minimum_revision=0):
    try:
        declaration = validate_service_plugin(declaration_bytes, manifest_bytes)
        snapshot = store.snapshot(declaration.execution_policy.id, minimum_revision=minimum_revision)
        policy = snapshot.policy
        if (
            policy.plugin_id != declaration.plugin_id
            or snapshot.policy_digest != declaration.execution_policy.digest
        ):
            raise ExecutionPolicyError("execution policy binding not verified")
        manifest = _load(manifest_bytes, 256 * 1024)
        rules = {rule.id: rule for rule in policy.capabilities}
        if set(rules) != {cap["id"] for cap in manifest["capabilities"]}:
            raise ExecutionPolicyError("execution policy capability binding not verified")
        for cap in manifest["capabilities"]:
            rule, permissions = rules[cap["id"]], frozenset(cap["permissions"])
            if (
                cap["kind"] != rule.kind
                or cap["version"] != rule.version
                or not rule.minimum_permissions <= permissions <= rule.allowed_permissions
                or cap["timeout_seconds"] > rule.timeout_seconds
            ):
                raise ExecutionPolicyError("execution policy permission or budget not verified")
        current = store.snapshot(declaration.execution_policy.id, minimum_revision=snapshot.revision)
        if current.revision != snapshot.revision or current.policy_digest != snapshot.policy_digest:
            raise ExecutionPolicyError("execution policy changed during resolution")
        return ResolvedExecutionPolicy(
            _digest(declaration_bytes),
            declaration.capability_manifest_digest,
            current.policy_digest,
            current.revision,
            current.policy,
            current.checked_at,
            current.policy.expires_at,
        )
    except (ServicePluginContractError, ValueError, TypeError, KeyError):
        raise ExecutionPolicyError("service execution policy resolution failed") from None


def execution_policy_schema():
    schema = _PolicyInput.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://xenon.jyxxe.com/schemas/service-execution-policy-v1.json"
    schema["description"] = (
        "Core execution constraints; parsing or resolution grants no admission or execution."
    )
    return schema
