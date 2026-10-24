"""Immutable local dependency evidence, never an authorization or runtime grant.

Inputs must later be bound to verified namespaces, signatures, live lifecycle
revisions and an approved plan by a protected coordinator. Nothing here fetches,
registers, imports or invokes a capability, including a self-labelled builtin.
"""

import hashlib
import json
from dataclasses import dataclass

from .capabilities import DEPENDENCY, CapabilityRegistry
from .capability_versions import satisfies
from .service_plugin_contract import MAX_MANIFEST_BYTES, _load

MAX_MANIFESTS = 32
MAX_CAPABILITIES = 512
MAX_DEPENDENCIES = 4096
MAX_TOTAL_BYTES = 2 * 1024 * 1024


class CapabilitySnapshotError(ValueError):
    """Stable safe diagnostic; untrusted manifest values are never echoed."""


def _digest(raw):
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class ManifestEvidence:
    plugin_id: str
    version: str
    source: str
    digest: str


@dataclass(frozen=True)
class SnapshotCapability:
    plugin_id: str
    manifest_digest: str
    id: str
    kind: str
    version: str
    runtime: str
    permissions: tuple[str, ...]
    dependencies: tuple[str, ...]
    timeout_seconds: int


@dataclass(frozen=True)
class DependencyResolution:
    snapshot_digest: str
    root_id: str
    # Dependencies precede their consumers; a shared dependency appears once.
    capabilities: tuple[SnapshotCapability, ...]


@dataclass(frozen=True)
class CapabilitySnapshot:
    digest: str
    manifests: tuple[ManifestEvidence, ...]
    capabilities: tuple[SnapshotCapability, ...]
    disabled: frozenset[str]

    def resolve(self, capability_id, *, require='*'):
        """Resolve only from this fixed snapshot; revalidation is external.

        Availability is caller-supplied snapshot data, not a live authority.
        Permission lists are declarations, never the caller's effective scopes.
        """
        if not isinstance(capability_id, str):
            raise CapabilitySnapshotError('invalid root capability')
        entries = {item.id: item for item in self.capabilities}
        root = entries.get(capability_id)
        if root is None:
            raise CapabilitySnapshotError('root capability missing')
        try:
            compatible = satisfies(root.version, require)
        except ValueError:
            raise CapabilitySnapshotError('invalid root version constraint') from None
        if not compatible:
            raise CapabilitySnapshotError('root capability incompatible')
        done, ordered = set(), []
        stack = [(capability_id, False)]
        while stack:
            identifier, leaving = stack.pop()
            if identifier in done:
                continue
            if identifier in self.disabled:
                raise CapabilitySnapshotError('capability closure disabled')
            item = entries[identifier]
            if leaving:
                done.add(identifier)
                ordered.append(item)
            else:
                stack.append((identifier, True))
                stack.extend((dependency.split('@', 1)[0], False)
                             for dependency in reversed(item.dependencies))
        return DependencyResolution(self.digest, capability_id, tuple(ordered))


def build_dependency_snapshot(manifest_bytes, *, disabled=()):
    """Validate a complete, bounded set, then freeze its exact-byte identities.

    A malformed/missing/incompatible/cyclic graph fails construction, even if
    the root a caller later chooses would not reach that error. Disabled entries
    remain in the graph so independent enabled closures can still be resolved.
    Order of manifest inputs and disabled ids does not affect the digest. Any
    change to raw manifest bytes (including whitespace) does affect it.
    """
    if not isinstance(manifest_bytes, (tuple, list)) or not 1 <= len(manifest_bytes) <= MAX_MANIFESTS:
        raise CapabilitySnapshotError('manifest count outside limit')
    if (any(not isinstance(raw, bytes) or len(raw) > MAX_MANIFEST_BYTES for raw in manifest_bytes)
            or sum(map(len, manifest_bytes)) > MAX_TOTAL_BYTES):
        raise CapabilitySnapshotError('manifest bytes outside limit')
    if not isinstance(disabled, (tuple, list, frozenset)) or len(disabled) > MAX_CAPABILITIES:
        raise CapabilitySnapshotError('disabled set outside limit')
    if any(not isinstance(identifier, str) for identifier in disabled) or len(set(disabled)) != len(disabled):
        raise CapabilitySnapshotError('invalid disabled set')
    manifests, entries, namespaces, edge_count = [], {}, set(), 0
    try:
        for raw in manifest_bytes:
            raw.decode('utf-8')  # Do not accept json.loads' UTF-16/32 autodetection.
            data = _load(raw, MAX_MANIFEST_BYTES)
            if not isinstance(data, dict) or set(data) - {
                'id', 'version', 'api_version', 'source', 'display', 'capabilities',
            }:
                raise ValueError
            capabilities = data.get('capabilities')
            if not isinstance(capabilities, list) or not 1 <= len(capabilities) <= 64:
                raise ValueError
            if len(entries) + len(capabilities) > MAX_CAPABILITIES:
                raise CapabilitySnapshotError('capability count outside limit')
            projected, dependencies = [], {}
            for capability in capabilities:
                if not isinstance(capability, dict) or set(capability) - {
                    'id', 'kind', 'version', 'runtime', 'permissions', 'dependencies', 'timeout_seconds',
                }:
                    raise ValueError
                deps = capability.get('dependencies', [])
                permissions = capability.get('permissions')
                if (not isinstance(deps, list) or len(deps) > 64
                        or any(not isinstance(dep, str) or not DEPENDENCY.fullmatch(dep) for dep in deps)
                        or len(set(deps)) != len(deps)
                        or not isinstance(permissions, list) or len(permissions) > 32
                        or any(not isinstance(p, str) or len(p) > 128 for p in permissions)
                        or len(set(permissions)) != len(permissions)):
                    raise ValueError
                edge_count += len(deps)
                if edge_count > MAX_DEPENDENCIES:
                    raise CapabilitySnapshotError('dependency count outside limit')
                identifier = capability.get('id')
                if not isinstance(identifier, str):
                    raise ValueError
                dependencies[identifier] = tuple(sorted(deps))
                # Reuse the existing entry/namespace/version/kind/budget checks.
                # Only cross-entry graph validation is deferred until all inputs
                # exist. The original bytes are never changed or rehashed here.
                projected.append({**capability, 'dependencies': []})
            validated = CapabilityRegistry({**data, 'capabilities': projected}).all()
            namespace = validated['id']
            if any(namespace == other or namespace.startswith(other + '.') or other.startswith(namespace + '.')
                   for other in namespaces):
                raise CapabilitySnapshotError('conflicting manifest namespace')
            namespaces.add(namespace)
            digest = _digest(raw)
            manifests.append(ManifestEvidence(namespace, validated['version'], validated['source'], digest))
            for item in validated['capabilities']:
                if item['id'] in entries:
                    raise CapabilitySnapshotError('conflicting capability id')
                entries[item['id']] = SnapshotCapability(
                    namespace, digest, item['id'], item['kind'], item['version'], item['runtime'],
                    tuple(sorted(item['permissions'])), dependencies[item['id']], item.get('timeout_seconds', 30),
                )
        for item in entries.values():
            for dependency in item.dependencies:
                target, constraint = dependency.split('@', 1)
                if target not in entries:
                    raise CapabilitySnapshotError('dependency missing')
                if not satisfies(entries[target].version, constraint):
                    raise CapabilitySnapshotError('dependency incompatible')
        try:
            CapabilityRegistry._reject_cycles({item.id: [dep.split('@', 1)[0] for dep in item.dependencies]
                                               for item in entries.values()})
        except ValueError:
            raise CapabilitySnapshotError('dependency cycle') from None
    except CapabilitySnapshotError:
        raise
    except (ValueError, TypeError, AttributeError, RecursionError):
        raise CapabilitySnapshotError('invalid capability manifests') from None
    if set(disabled) - entries.keys():
        raise CapabilitySnapshotError('disabled capability missing')
    manifests = tuple(sorted(manifests, key=lambda item: item.plugin_id))
    # A versioned domain separates this evidence from signatures/policy/plan hashes.
    identity = {'schema_version': '1', 'type': 'capability-dependency-snapshot',
                'manifests': [{'id': item.plugin_id, 'digest': item.digest} for item in manifests],
                'disabled': sorted(disabled)}
    digest = _digest(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode())
    return CapabilitySnapshot(digest, manifests, tuple(entries[key] for key in sorted(entries)), frozenset(disabled))
