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
        try:
            manifest = json.loads(row["manifest"])
        except (TypeError, ValueError):
            continue
        if not isinstance(manifest, dict):
            continue
        permissions = [permission for permission in manifest.get("permissions") or [] if isinstance(permission, str)]
        if not row["enabled"] or not set(permissions) <= scopes:
            continue
        version = str(manifest.get("version") or "0.0.0")
        for kind, key in (("app", "app"), ("widget", "widget")):
            section = manifest.get(key)
            if not isinstance(section, dict) or not isinstance(section.get("id"), str) or not section["id"]:
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
                "installed": True,
                "enabled": True,
            })
    return items


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
    def get_capability(capability_id: str, request: Request, actor=Depends(scope("desktop:read"))):
        registry = load_registry()
        capability = registry.lookup(capability_id)
        if capability is None:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such capability")
        return response(request, capability)
