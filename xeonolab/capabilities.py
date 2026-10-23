"""Read-only Capability Registry MVP (Phase 1 of Epic #305).

Aggregates the builtin capability manifest into a validated, discoverable list.
Existing plugin/app APIs stay authoritative; this module only reads and validates
the declarative manifest (no code execution, no runtime behaviour change).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import Depends, Request

from .common import APIError, connect, response, scope
from .plugins import _installed_manifest

KINDS = {"app", "widget", "tool", "agent", "provider", "worker"}
TIERS = {"ui", "api", "service"}
PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")
CAPABILITY_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$")
PERMISSION = re.compile(r"^[a-z0-9-]+:[a-z0-9-]+$")
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "infra/plugins/builtin-capabilities.json"


class CapabilityRegistry:
    def __init__(self, manifest):
        self.manifest = self._validate(manifest)

    @staticmethod
    def _validate(manifest):
        if not isinstance(manifest, dict):
            raise ValueError("manifest must be an object")
        if manifest.get("api_version") != "1":
            raise ValueError("unsupported api_version")
        if not PLUGIN_ID.fullmatch(str(manifest.get("id", ""))):
            raise ValueError("invalid manifest id")
        if not SEMVER.fullmatch(str(manifest.get("version", ""))):
            raise ValueError("invalid manifest version")
        if manifest.get("source") not in ("builtin", "declared"):
            raise ValueError("invalid manifest source")
        capabilities = manifest.get("capabilities")
        if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 64:
            raise ValueError("capabilities must be a non-empty list")
        seen, graph = set(), {}
        for capability in capabilities:
            identifier = capability.get("id")
            if not isinstance(identifier, str) or not CAPABILITY_ID.fullmatch(identifier):
                raise ValueError(f"invalid capability id {identifier!r}")
            if not (identifier == manifest["id"] or identifier.startswith(manifest["id"] + ".")):
                raise ValueError(f"capability {identifier} outside manifest namespace")
            if identifier in seen:
                raise ValueError(f"duplicate capability {identifier}")
            seen.add(identifier)
            if capability.get("kind") not in KINDS:
                raise ValueError(f"invalid kind for {identifier}")
            if not SEMVER.fullmatch(str(capability.get("version", ""))):
                raise ValueError(f"invalid version for {identifier}")
            if capability.get("runtime") not in TIERS:
                raise ValueError(f"invalid runtime for {identifier}")
            permissions = capability.get("permissions")
            if not isinstance(permissions, list) or any(not PERMISSION.fullmatch(str(p)) for p in permissions):
                raise ValueError(f"invalid permissions for {identifier}")
            dependencies = capability.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise ValueError(f"invalid dependencies for {identifier}")
            graph[identifier] = [str(dep).split("@", 1)[0] for dep in dependencies]
        CapabilityRegistry._reject_cycles(graph)
        return {
            "id": manifest["id"],
            "version": manifest["version"],
            "api_version": "1",
            "source": manifest["source"],
            "display": manifest.get("display") or {},
            "capabilities": [
                {
                    "id": capability["id"],
                    "kind": capability["kind"],
                    "version": capability["version"],
                    "runtime": capability["runtime"],
                    "permissions": list(capability["permissions"]),
                    "dependencies": list(capability.get("dependencies", [])),
                    **({"timeout_seconds": capability["timeout_seconds"]} if "timeout_seconds" in capability else {}),
                }
                for capability in capabilities
            ],
        }

    @staticmethod
    def _reject_cycles(graph):
        visiting, done = set(), set()

        def visit(node):
            if node in done:
                return
            if node in visiting:
                raise ValueError(f"dependency cycle at {node}")
            visiting.add(node)
            for dependency in graph.get(node, []):
                visit(dependency)
            visiting.discard(node)
            done.add(node)

        for node in graph:
            visit(node)

    def all(self):
        return self.manifest

    def lookup(self, capability_id, version=None):
        for capability in self.manifest["capabilities"]:
            if capability["id"] == capability_id:
                if version is not None and capability["version"] != version:
                    return None
                return capability
        return None

    def by_kind(self, kind):
        return [capability for capability in self.manifest["capabilities"] if capability["kind"] == kind]

    def summary(self):
        counts = {kind: 0 for kind in KINDS}
        for capability in self.manifest["capabilities"]:
            counts[capability["kind"]] += 1
        return {"manifest": {"id": self.manifest["id"], "version": self.manifest["version"]}, "counts": counts}


def runtime_capabilities(registry, database, owner, scopes):
    """Builtin capabilities plus this account's enabled plugin capabilities.

    Disabled plugins and plugins whose permissions exceed the caller's current
    scopes are omitted, so a stopped plugin stops being discoverable. Malformed
    install snapshots are skipped rather than breaking discovery.
    """
    scopes = set(scopes or [])
    items = []
    for capability in registry.all()["capabilities"]:
        items.append({
            **capability,
            "source": "builtin",
            "installed": True,
            "enabled": set(capability["permissions"]) <= scopes,
        })
    try:
        with connect(database) as db:
            rows = db.execute(
                "SELECT plugin_id,enabled,manifest FROM plugin_installs WHERE owner=? ORDER BY plugin_id",
                (owner,),
            ).fetchall()
    except Exception:  # noqa: BLE001 - a missing plugin store must not break discovery
        rows = []
    for row in rows:
        manifest = _installed_manifest(row)
        if manifest is None or not row["enabled"] or not set(manifest.permissions) <= scopes:
            continue
        permissions = manifest.permissions
        version = manifest.version
        for kind in ("app", "widget"):
            descriptor = getattr(manifest, kind)
            if descriptor is None:
                continue
            items.append({
                "id": f"plugin.{row['plugin_id']}.{kind}",
                "kind": kind,
                "version": version,
                "runtime": "ui",
                "permissions": permissions,
                "dependencies": [],
                "source": "plugin",
                "plugin_id": row["plugin_id"],
                "desktop_id": descriptor.id,
                "installed": True,
                "enabled": True,
            })
    # Administrator-managed iframe entries remain a supported source. Their
    # descriptors still come from /apps; discovery supplies availability only.
    with connect(database) as db:
        table_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='apps'"
        ).fetchone()
        rows = db.execute("SELECT definition FROM apps ORDER BY id").fetchall() if table_exists else []
    for row in rows:
        try:
            entry = json.loads(row["definition"])
            permissions = entry["permissions"]
            if not isinstance(permissions, list) or not set(permissions) <= scopes:
                continue
            identifier = entry["id"]
            if not isinstance(identifier, str) or not identifier:
                continue
        except (TypeError, ValueError, KeyError):
            continue
        items.append({
            "id": f"registered.platform.app.{identifier}", "kind": "app",
            "version": "1.0.0", "runtime": "ui", "permissions": permissions,
            "dependencies": [], "source": "registered", "desktop_id": identifier,
            "installed": True, "enabled": True,
        })
    return items



def satisfies(version, constraint):
    """Minimal semver constraint check for capability compatibility.

    Supported: exact ("1.2.3"), caret ("^1.2.3" -> same major, >= version),
    tilde ("~1.2.3" -> same major.minor, >= version) and wildcard "*".
    Anything else raises ValueError so callers fail closed.
    """
    def parts(value):
        core = value.split("-", 1)[0]
        pieces = core.split(".")
        if len(pieces) != 3 or not all(piece.isdigit() for piece in pieces):
            raise ValueError(f"invalid semver: {value}")
        return tuple(int(piece) for piece in pieces)

    current = parts(version)
    if constraint == "*":
        return True
    if constraint.startswith("^"):
        target = parts(constraint[1:])
        return current[0] == target[0] and current >= target
    if constraint.startswith("~"):
        target = parts(constraint[1:])
        return current[:2] == target[:2] and current >= target
    return current == parts(constraint)


class CapabilityIncompatible(APIError):
    def __init__(self, capability_id, version, constraint):
        super().__init__(
            409,
            "CAPABILITY_INCOMPATIBLE",
            f"capability {capability_id} version {version} does not satisfy {constraint}",
        )

def load_registry(path=None):
    """Load the builtin manifest; missing/invalid manifests fail closed with 503."""
    target = Path(path) if path is not None else DEFAULT_MANIFEST
    try:
        manifest = json.loads(target.read_text())
    except (OSError, ValueError) as error:
        raise APIError(503, "CAPABILITY_REGISTRY_UNAVAILABLE", "capability manifest is not readable") from error
    try:
        return CapabilityRegistry(manifest)
    except ValueError as error:
        raise APIError(503, "CAPABILITY_REGISTRY_INVALID", f"capability manifest is invalid: {error}") from error


def install(app, plugin_database=None):
    @app.get("/api/v1/capabilities")
    def list_capabilities(request: Request, actor=Depends(scope("desktop:read"))):
        registry = load_registry()
        capabilities = (
            runtime_capabilities(registry, plugin_database, actor.sub, actor.scopes)
            if plugin_database is not None
            else registry.all()["capabilities"]
        )
        return response(request, capabilities, **{"registry": registry.summary()})

    @app.get("/api/v1/capabilities/{capability_id}")
    def get_capability(
        capability_id: str,
        request: Request,
        require: str = "",
        actor=Depends(scope("desktop:read")),
    ):
        registry = load_registry()
        capabilities = (
            runtime_capabilities(registry, plugin_database, actor.sub, actor.scopes)
            if plugin_database is not None
            else [
                item for item in registry.all()["capabilities"]
                if set(item["permissions"]) <= set(actor.scopes)
            ]
        )
        capability = next(
            (item for item in capabilities if item["id"] == capability_id and item.get("enabled", True)),
            None,
        )
        if capability is None:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such capability")
        compatible = None
        if require:
            try:
                compatible = satisfies(capability["version"], require)
            except ValueError as error:
                raise APIError(422, "INVALID_VERSION_CONSTRAINT", str(error)) from None
            if not compatible:
                raise CapabilityIncompatible(capability_id, capability["version"], require)
        return response(request, capability, compatible=compatible if require else None)
