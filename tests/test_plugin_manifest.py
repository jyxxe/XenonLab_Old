"""Phase 0 contract for the universal plugin architecture (Epic #305).

The schema is the machine-readable contract; these tests validate the example
against the schema's rules without adding a runtime dependency on a JSON-Schema
library (the Phase 1 registry will use the same checks in code).
"""

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / "infra/plugins/capability-manifest.schema.json").read_text())
EXAMPLE = json.loads((ROOT / "infra/plugins/capability-manifest.example.json").read_text())

SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(-[0-9A-Za-z.-]+)?$")
CAPABILITY = re.compile(r"^[a-z0-9][a-z0-9._-]{2,127}$")
PLUGIN_ID = re.compile(r"^[a-z0-9][a-z0-9.-]{1,62}$")


def manifest_errors(manifest):
    """Validate the documented manifest rules (mirrors the JSON Schema)."""
    errors = []
    for field in SCHEMA["required"]:
        if field not in manifest:
            errors.append(f"missing {field}")
    if manifest.get("api_version") != "1":
        errors.append("unknown api_version")
    if not PLUGIN_ID.fullmatch(str(manifest.get("id", ""))):
        errors.append("invalid plugin id")
    if not SEMVER.fullmatch(str(manifest.get("version", ""))):
        errors.append("invalid plugin version")
    if manifest.get("source") not in ("builtin", "declared"):
        errors.append("invalid source")
    capabilities = manifest.get("capabilities")
    if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 64:
        errors.append("capabilities must be a non-empty list")
        return errors
    seen = set()
    for capability in capabilities:
        for field in ("id", "kind", "version", "runtime", "permissions", "dependencies"):
            if field not in capability:
                errors.append(f"capability missing {field}")
        if not CAPABILITY.fullmatch(str(capability.get("id", ""))):
            errors.append(f"invalid capability id {capability.get('id')}")
        if capability.get("id") in seen:
            errors.append(f"duplicate capability {capability.get('id')}")
        seen.add(capability.get("id"))
        if capability.get("kind") not in ("app", "widget", "tool", "agent", "provider", "worker"):
            errors.append("invalid capability kind")
        if not SEMVER.fullmatch(str(capability.get("version", ""))):
            errors.append("invalid capability version")
        if capability.get("runtime") not in ("ui", "api", "service"):
            errors.append("invalid runtime tier")
        if not isinstance(capability.get("permissions"), list):
            errors.append("permissions must be a list")
        if not isinstance(capability.get("dependencies"), list):
            errors.append("dependencies must be a list")
    return errors


def test_example_manifest_validates_and_capabilities_share_the_plugin_namespace():
    assert manifest_errors(EXAMPLE) == []
    namespace = EXAMPLE["id"]
    for capability in EXAMPLE["capabilities"]:
        assert capability["id"].startswith(namespace + ".")


def test_schema_rejects_unknown_api_versions_and_malformed_versions():
    assert manifest_errors({**EXAMPLE, "api_version": "2"}), "unknown api_version must be rejected"
    bad = json.loads(json.dumps(EXAMPLE))
    bad["capabilities"][0]["version"] = "1.0"
    assert manifest_errors(bad), "non-semver capability version must be rejected"


def test_duplicate_capabilities_are_rejected():
    duplicate = json.loads(json.dumps(EXAMPLE))
    duplicate["capabilities"].append(dict(duplicate["capabilities"][0]))
    assert manifest_errors(duplicate)


def test_dependency_cycles_are_detectable_by_the_registry_rule():
    def has_cycle(graph):
        visiting, done = set(), set()

        def visit(node):
            if node in done:
                return False
            if node in visiting:
                return True
            visiting.add(node)
            for dep in graph.get(node, []):
                if visit(dep):
                    return True
            visiting.discard(node)
            done.add(node)
            return False

        return any(visit(node) for node in graph)

    assert has_cycle({"a": ["b"], "b": []}) is False
    assert has_cycle({"a": ["b"], "b": ["a"]}) is True


def test_runtime_tiers_are_known_and_manifest_has_no_secret_values():
    assert {capability["runtime"] for capability in EXAMPLE["capabilities"]} <= {"ui", "api", "service"}
    assert "secret" not in json.dumps(EXAMPLE).lower()
