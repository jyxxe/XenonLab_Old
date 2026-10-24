"""Offline Phase 3 proposal validation, never service admission or execution.

Only a protected coordinator may later bind these declarations to trusted policy,
verified provenance, deployment authority and a fenced routing generation.
"""

import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .capabilities import PLUGIN_ID, SEMVER, CapabilityRegistry, satisfies
from .capability_versions import MAX_VERSION_LENGTH

DIGEST = r"^sha256:[a-f0-9]{64}$"
MAX_DECLARATION_BYTES = 16 * 1024
MAX_MANIFEST_BYTES = 256 * 1024


class PolicyReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{1,63}$")
    digest: str = Field(pattern=DIGEST)


class ServicePluginDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["1"]
    plugin_id: str = Field(pattern=PLUGIN_ID.pattern)
    version: str = Field(pattern=SEMVER.pattern, max_length=MAX_VERSION_LENGTH,
                         json_schema_extra={"not": {"pattern": r"\s"}})
    artifact_format: Literal["oci"]
    artifact_digest: str = Field(pattern=DIGEST)
    capability_manifest_digest: str = Field(pattern=DIGEST)
    release_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    # This reference is a request, not an authority or proof of approval.
    execution_policy: PolicyReference


class ServicePluginContractError(ValueError):
    """Safe diagnostic that contains no untrusted JSON values or file contents."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ServicePluginContractError("duplicate JSON member")
        result[key] = value
    return result


def _reject_constant(value):
    raise ServicePluginContractError("non-finite JSON number")


def _load(raw, limit):
    if not isinstance(raw, bytes) or len(raw) > limit:
        raise ServicePluginContractError("JSON input exceeds byte limit or is not bytes")
    try:
        return json.loads(raw, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise ServicePluginContractError("invalid JSON document") from None


def validate_service_plugin(declaration_bytes, manifest_bytes):
    """Return a checked declaration. No signature, policy or runtime is trusted.

    The manifest digest is over the exact supplied bytes, not reserialized JSON.
    Caller-provided paths/URLs are never interpreted; artifact/profile digests
    are not fetched, executed or treated as evidence that their objects exist.
    """
    declaration = _load(declaration_bytes, MAX_DECLARATION_BYTES)
    try:
        declaration = ServicePluginDeclaration.model_validate(declaration)
    except ValidationError:
        raise ServicePluginContractError("invalid service declaration") from None
    manifest = _load(manifest_bytes, MAX_MANIFEST_BYTES)
    expected = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    if declaration.capability_manifest_digest != expected:
        raise ServicePluginContractError("capability manifest digest mismatch")
    try:
        if not isinstance(manifest, dict) or set(manifest) - {
            "id", "version", "api_version", "source", "display", "capabilities",
        }:
            raise ValueError
        if manifest.get("source") != "declared":
            raise ValueError
        if manifest.get("id") != declaration.plugin_id or manifest.get("version") != declaration.version:
            raise ValueError
        display = manifest.get("display", {})
        if not isinstance(display, dict) or set(display) - {"title", "description", "icon"}:
            raise ValueError
        for field, maximum in (("title", 80), ("description", 400), ("icon", 8)):
            if field in display and (
                not isinstance(display[field], str) or len(display[field]) > maximum
                or (field == "title" and not display[field])
            ):
                raise ValueError
        capabilities = manifest.get("capabilities")
        if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 64:
            raise ValueError
        for capability in capabilities:
            if not isinstance(capability, dict) or set(capability) - {
                "id", "kind", "version", "runtime", "permissions", "dependencies", "timeout_seconds",
            }:
                raise ValueError
            if capability.get("runtime") != "service" or capability.get("kind") not in {
                "tool", "agent", "provider", "worker",
            }:
                raise ValueError
            timeout = capability.get("timeout_seconds")
            if type(timeout) is not int or not 1 <= timeout <= 300:
                raise ValueError
            permissions = capability.get("permissions")
            if not isinstance(permissions, list) or any(not isinstance(p, str) for p in permissions):
                raise ValueError
            if len(permissions) > 32 or len(permissions) != len(set(permissions)):
                raise ValueError
            dependencies = capability.get("dependencies")
            if not isinstance(dependencies, list) or len(dependencies) > 64:
                raise ValueError
            if any(not isinstance(d, str) or not re.fullmatch(
                r"[a-z0-9][a-z0-9._-]{2,127}@[~^]?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\."
                r"(0|[1-9][0-9]*)", d,
            ) for d in dependencies) or len(dependencies) != len(set(dependencies)):
                raise ValueError
        # Offline validation is not the complete runtime registry. Preserve
        # original bytes/digests; only this private view defers foreign namespaces
        # to eventual global admission. Execution registry rules are unchanged.
        namespace = manifest["id"]
        versions = {item.get("id"): item.get("version") for item in capabilities}
        local_capabilities = []
        for capability in capabilities:
            local_dependencies = []
            for dependency in capability.get("dependencies", []):
                target, constraint = dependency.split("@", 1)
                if target == namespace or target.startswith(namespace + "."):
                    if (target not in versions or not isinstance(versions[target], str)
                            or "-" in versions[target] or not satisfies(versions[target], constraint)):
                        raise ValueError
                    local_dependencies.append(dependency)
            local_capabilities.append({**capability, "dependencies": local_dependencies})
        CapabilityRegistry({**manifest, "capabilities": local_capabilities})
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise ServicePluginContractError("invalid service capability manifest") from None
    return declaration


def declaration_schema():
    schema = ServicePluginDeclaration.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = "https://xenon.jyxxe.com/schemas/service-plugin-declaration-v1.json"
    schema["description"] = "Offline proposed contract only; validation grants no trust, deployment or execution rights."
    return schema
