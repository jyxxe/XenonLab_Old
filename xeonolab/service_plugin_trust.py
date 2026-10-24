"""Core-owned, explicitly provisioned public signing policy; no HTTP install hook.

No publisher selects this database or mutates it. Verification reads a fresh
bounded snapshot. Management needs an authenticated interactive human with a
separate permission and an explicit core rollout gate. No scope is provisioned.
"""

import base64
import binascii
import hashlib
import math
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .common import APIError, identity, transaction
from .service_plugin_contract import DIGEST, PLUGIN_ID, ServicePluginContractError, _load

MAX_POLICY_BYTES = 64 * 1024
MAX_REVISION = 2**63 - 1
MAX_REVOCATIONS = 4096
MAX_POLICY_LIFETIME = 86400
Digest = Annotated[str, Field(pattern=DIGEST, min_length=71, max_length=71)]
PluginId = Annotated[str, Field(pattern=PLUGIN_ID.pattern, min_length=2, max_length=63)]
SOURCE_BUILD_TYPE = "https://xenon.jyxxe.com/buildtypes/oci-source/v1"


class ServiceSignatureError(ValueError):
    """A safe fixed diagnostic: never includes an untrusted input or DB path."""


def digest(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def decode_base64(value, maximum):
    if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
        raise ServiceSignatureError("invalid base64 value")
    if not re.fullmatch(r"[A-Za-z0-9+/_-]*={0,2}", value) or len(value) % 4 == 1:
        raise ServiceSignatureError("invalid base64 value")
    if "=" in value and len(value) % 4:
        raise ServiceSignatureError("invalid base64 value")
    try:
        raw = base64.b64decode(
            value.translate(str.maketrans("-_", "+/")) + "=" * (-len(value) % 4), validate=True
        )
    except (binascii.Error, ValueError):
        raise ServiceSignatureError("invalid base64 value") from None
    if len(raw) > maximum:
        raise ServiceSignatureError("invalid base64 value")
    return raw


class _BuilderInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    builder_id: str = Field(min_length=1, max_length=512)
    repository: str = Field(min_length=1, max_length=512)
    build_config: str = Field(min_length=1, max_length=256)
    plugin_ids: list[PluginId] = Field(min_length=1, max_length=32, json_schema_extra={"uniqueItems": True})


class _KeyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    algorithm: Literal["ed25519"]
    public_key: str = Field(
        pattern=r"^[A-Za-z0-9+/_-]+={0,2}$",
        min_length=43,
        max_length=44,
        json_schema_extra={"not": {"pattern": r"\s"}},
    )
    plugin_ids: list[PluginId] = Field(min_length=1, max_length=32, json_schema_extra={"uniqueItems": True})
    not_before: int = Field(ge=0, le=MAX_REVISION)
    expires_at: int = Field(gt=0, le=MAX_REVISION)
    provenance_builders: list[_BuilderInput] = Field(default_factory=list, max_length=8)


class _PolicyInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1"]
    issued_at: int = Field(ge=0, le=MAX_REVISION)
    expires_at: int = Field(gt=0, le=MAX_REVISION)
    threshold: int = Field(ge=1, le=16)
    keys: list[_KeyInput] = Field(max_length=32)
    revoked_keys: list[Digest] = Field(
        default_factory=list, max_length=256, json_schema_extra={"uniqueItems": True}
    )
    revoked_artifacts: list[Digest] = Field(
        default_factory=list, max_length=256, json_schema_extra={"uniqueItems": True}
    )


@dataclass(frozen=True)
class ProvenanceBuilder:
    builder_id: str
    repository: str
    build_config: str
    plugin_ids: frozenset[str]


@dataclass(frozen=True)
class TrustedKey:
    fingerprint: str
    public_key: bytes = field(repr=False)
    plugin_ids: frozenset[str]
    not_before: int
    expires_at: int
    provenance_builders: tuple[ProvenanceBuilder, ...] = ()


@dataclass(frozen=True)
class TrustPolicy:
    issued_at: int
    expires_at: int
    threshold: int
    keys: tuple[TrustedKey, ...]
    revoked_keys: frozenset[str]
    revoked_artifacts: frozenset[str]


def parse_policy(raw):
    try:
        if not isinstance(raw, bytes) or len(raw) > MAX_POLICY_BYTES:
            raise ValueError
        raw.decode("utf-8")
        data = _PolicyInput.model_validate(_load(raw, MAX_POLICY_BYTES))
        if not 0 < data.expires_at - data.issued_at <= MAX_POLICY_LIFETIME:
            raise ValueError
        keys, fingerprints = [], set()
        for entry in data.keys:
            public = decode_base64(entry.public_key, 32)
            fingerprint = digest(public)
            if len(public) != 32 or fingerprint in fingerprints or entry.not_before >= entry.expires_at:
                raise ValueError
            if len(set(entry.plugin_ids)) != len(entry.plugin_ids) or any(
                not PLUGIN_ID.fullmatch(p) or p == "builtin" or p.startswith("builtin.")
                for p in entry.plugin_ids
            ):
                raise ValueError
            fingerprints.add(fingerprint)
            builders, seen_builders = [], set()
            for rule in entry.provenance_builders:
                for value in (rule.builder_id, rule.repository):
                    parsed = urlsplit(value)
                    if (not value.startswith("https://") or not parsed.hostname or parsed.username is not None
                            or parsed.password is not None or parsed.query or parsed.fragment
                            or any(character.isspace() or ord(character) < 32 for character in value)):
                        raise ValueError
                    parsed.port  # Reject malformed/out-of-range ports, without resolving a host.
                parts = rule.build_config.split("/")
                if (not re.fullmatch(r"[A-Za-z0-9._/-]+", rule.build_config)
                        or any(part in ("", ".", "..") for part in parts)
                        or len(set(rule.plugin_ids)) != len(rule.plugin_ids)
                        or not set(rule.plugin_ids) <= set(entry.plugin_ids)):
                    raise ValueError
                builder = ProvenanceBuilder(rule.builder_id, rule.repository, rule.build_config,
                                            frozenset(rule.plugin_ids))
                if builder in seen_builders:
                    raise ValueError
                seen_builders.add(builder)
                builders.append(builder)
            keys.append(
                TrustedKey(
                    fingerprint, public, frozenset(entry.plugin_ids), entry.not_before, entry.expires_at,
                    tuple(builders),
                )
            )
        if any(len(set(values)) != len(values) for values in (data.revoked_keys, data.revoked_artifacts)):
            raise ValueError
        return TrustPolicy(
            data.issued_at,
            data.expires_at,
            data.threshold,
            tuple(keys),
            frozenset(data.revoked_keys),
            frozenset(data.revoked_artifacts),
        )
    except (ValueError, UnicodeError, ValidationError, ServicePluginContractError):
        raise ServiceSignatureError("invalid trust policy") from None


@dataclass(frozen=True)
class TrustSnapshot:
    revision: int
    policy_digest: str
    policy: TrustPolicy
    revoked_keys: frozenset[str]
    revoked_artifacts: frozenset[str]
    checked_at: int


class TrustPolicyStore:
    def __init__(self, path, *, clock=time.time, management_enabled=False, initialize=False):
        if type(management_enabled) is not bool or type(initialize) is not bool:
            raise ValueError("trust store gates must be booleans")
        self.path = Path(path)
        self._clock = clock
        self.management_enabled = management_enabled
        if initialize:
            with transaction(self.path) as db:
                db.executescript("""
                    CREATE TABLE IF NOT EXISTS service_trust_meta(
                        singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL);
                    CREATE TABLE IF NOT EXISTS service_trust_policies(
                        revision INTEGER PRIMARY KEY, digest TEXT NOT NULL, body BLOB NOT NULL);
                    CREATE TABLE IF NOT EXISTS service_trust_revocations(
                        kind TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(kind,digest));
                    CREATE TABLE IF NOT EXISTS service_trust_audit(
                        actor TEXT NOT NULL, operation_id TEXT NOT NULL, expected_revision INTEGER NOT NULL,
                        applied_revision INTEGER NOT NULL, digest TEXT NOT NULL, at INTEGER NOT NULL,
                        PRIMARY KEY(actor,operation_id));
                """)
                if db.execute("SELECT revision FROM service_trust_meta WHERE singleton=1").fetchone() is None:
                    if any(
                        db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
                        for table in (
                            "service_trust_policies",
                            "service_trust_revocations",
                            "service_trust_audit",
                        )
                    ):
                        raise ServiceSignatureError("trust policy unavailable")
                    db.execute("INSERT INTO service_trust_meta VALUES(1,0)")

    def now(self):
        try:
            value = self._clock()
        except Exception:
            raise ServiceSignatureError("trust clock unavailable") from None
        if type(value) not in (int, float) or not 0 <= value <= MAX_REVISION or not math.isfinite(value):
            raise ServiceSignatureError("trust clock unavailable")
        return int(value)

    @staticmethod
    def _revision(db):
        row = db.execute("SELECT revision FROM service_trust_meta WHERE singleton=1").fetchone()
        if row is None or type(row[0]) is not int or not 0 <= row[0] <= MAX_REVISION:
            raise ServiceSignatureError("trust policy unavailable")
        return row[0]

    @staticmethod
    def _revocations(db):
        rows = db.execute(
            "SELECT kind,digest FROM service_trust_revocations LIMIT ?", (MAX_REVOCATIONS + 1,)
        ).fetchall()
        if len(rows) > MAX_REVOCATIONS or any(
            row[0] not in ("key", "artifact")
            or not isinstance(row[1], str)
            or not re.fullmatch(DIGEST, row[1])
            for row in rows
        ):
            raise ServiceSignatureError("trust revocation state unavailable")
        return frozenset(row[1] for row in rows if row[0] == "key"), frozenset(
            row[1] for row in rows if row[0] == "artifact"
        )

    def snapshot(self, *, minimum_revision=0):
        if type(minimum_revision) is not int or not 0 <= minimum_revision <= MAX_REVISION:
            raise ServiceSignatureError("invalid trust revision floor")
        try:
            # mode=ro prevents a missing trust database from being bootstrapped
            # by a verifier; the caller fixes this path outside publisher data.
            with closing(
                sqlite3.connect(self.path.absolute().as_uri() + "?mode=ro", uri=True, timeout=5)
            ) as db:
                db.execute("BEGIN")
                revision = self._revision(db)
                if not revision or revision < minimum_revision:
                    raise ServiceSignatureError("trust policy unavailable")
                row = db.execute(
                    "SELECT digest,body FROM service_trust_policies WHERE revision=?", (revision,)
                ).fetchone()
                if row is None or not isinstance(row[1], bytes) or digest(row[1]) != row[0]:
                    raise ServiceSignatureError("trust policy unavailable")
                policy = parse_policy(row[1])
                revoked_keys, revoked_artifacts = self._revocations(db)
                if (
                    not policy.revoked_keys <= revoked_keys
                    or not policy.revoked_artifacts <= revoked_artifacts
                ):
                    raise ServiceSignatureError("trust revocation state unavailable")
                now = self.now()
                if not policy.issued_at <= now < policy.expires_at:
                    raise ServiceSignatureError("trust policy expired or not active")
                return TrustSnapshot(revision, row[0], policy, revoked_keys, revoked_artifacts, now)
        except (sqlite3.Error, OSError):
            raise ServiceSignatureError("trust policy unavailable") from None

    def publish(self, raw, *, expected_revision, operation_id, request):
        actor = identity(request)
        actor.require("plugin-service:manage")
        if actor.kind != "human" or request.headers.get("Authorization", "").startswith("Bearer xek_"):
            raise APIError(
                403, "INTERACTIVE_LOGIN_REQUIRED", "human OIDC login required for trust policy changes"
            )
        if not self.management_enabled:
            raise APIError(503, "SERVICE_TRUST_NOT_ENABLED", "trust management rollout is not enabled")
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION:
            raise APIError(422, "INVALID_TRUST_REVISION", "invalid expected trust revision")
        try:
            operation = str(UUID(str(operation_id)))
        except (ValueError, TypeError, AttributeError):
            raise APIError(422, "INVALID_TRUST_OPERATION", "UUID operation id required") from None
        policy, policy_digest = parse_policy(raw), digest(raw)
        try:
            with transaction(self.path) as db:
                revision = self._revision(db)
                previous = db.execute(
                    "SELECT expected_revision,applied_revision,digest FROM service_trust_audit "
                    "WHERE actor=? AND operation_id=?",
                    (actor.sub, operation),
                ).fetchone()
                if previous:
                    if (
                        previous["expected_revision"] != expected_revision
                        or previous["digest"] != policy_digest
                    ):
                        raise APIError(
                            409, "TRUST_OPERATION_CONFLICT", "operation id reused with different input"
                        )
                    return {
                        "revision": revision,
                        "applied_revision": previous["applied_revision"],
                        "duplicate": True,
                    }
                if revision != expected_revision:
                    raise APIError(409, "TRUST_REVISION_CONFLICT", "trust policy revision changed")
                if revision == MAX_REVISION:
                    raise APIError(409, "TRUST_REVISION_EXHAUSTED", "trust revision exhausted")
                now = self.now()
                if not policy.issued_at <= now < policy.expires_at:
                    raise ServiceSignatureError("trust policy expired or not active")
                revoked_keys, _ = self._revocations(db)
                if any(key.fingerprint in revoked_keys | policy.revoked_keys for key in policy.keys):
                    raise APIError(409, "TRUST_KEY_REVOKED", "revoked key cannot be restored")
                for kind, values in (("key", policy.revoked_keys), ("artifact", policy.revoked_artifacts)):
                    db.executemany(
                        "INSERT OR IGNORE INTO service_trust_revocations VALUES(?,?)",
                        ((kind, value) for value in values),
                    )
                self._revocations(db)  # Enforce the bounded permanent-tombstone budget transactionally.
                revision += 1
                db.execute("INSERT INTO service_trust_policies VALUES(?,?,?)", (revision, policy_digest, raw))
                db.execute("UPDATE service_trust_meta SET revision=? WHERE singleton=1", (revision,))
                db.execute(
                    "INSERT INTO service_trust_audit VALUES(?,?,?,?,?,?)",
                    (actor.sub, operation, expected_revision, revision, policy_digest, now),
                )
                return {"revision": revision, "applied_revision": revision, "duplicate": False}
        except (sqlite3.Error, OSError):
            raise ServiceSignatureError("trust policy write unavailable") from None


def trust_policy_schema():
    schema = _PolicyInput.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://xenon.jyxxe.com/schemas/service-signature-trust-v1.json"
    schema["description"] = (
        "Core public signing policy input; syntactic validation grants no trust or execution."
    )
    return schema
