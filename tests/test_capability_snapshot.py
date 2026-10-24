"""Bounded global dependency evidence must not become a mutable runtime grant."""

import copy
import json
from dataclasses import FrozenInstanceError

import pytest

from xeonolab import capability_snapshot as snapshots
from xeonolab.capabilities import CapabilityRegistry, load_registry
from xeonolab.capability_snapshot import CapabilitySnapshotError, build_dependency_snapshot


def manifest(namespace='org.alpha', *, dependencies=(), version='1.0.0'):
    return {'id': namespace, 'version': '1.0.0', 'api_version': '1', 'source': 'declared',
            'capabilities': [{'id': namespace + '.tool', 'version': version, 'kind': 'tool',
                              'runtime': 'service', 'permissions': ['xelibrary:read'],
                              'dependencies': list(dependencies), 'timeout_seconds': 30}]}


def raw(data):
    return json.dumps(data, separators=(',', ':')).encode()


def graph():
    return [manifest(dependencies=['org.beta.tool@^1.0.0', 'org.gamma.tool@~1.0.0']),
            manifest('org.beta', dependencies=['org.delta.tool@1.0.0']),
            manifest('org.gamma', dependencies=['org.delta.tool@1.0.0']), manifest('org.delta')]


def test_global_diamond_exact_bytes_topological_dedup_and_input_order():
    data = list(map(raw, graph()))
    snapshot = build_dependency_snapshot(data)
    result = snapshot.resolve('org.alpha.tool', require='^1.0.0')
    assert [c.id for c in result.capabilities] == [
        'org.delta.tool', 'org.beta.tool', 'org.gamma.tool', 'org.alpha.tool']
    assert result.snapshot_digest == snapshot.digest
    assert result.capabilities[-1].permissions == ('xelibrary:read',)
    assert build_dependency_snapshot(list(reversed(data))) == snapshot
    # Byte identity remains exact, even when parsed JSON is semantically equal.
    changed = build_dependency_snapshot([b' ' + data[0], *data[1:]])
    assert changed.digest != snapshot.digest
    assert changed.capabilities[-1].manifest_digest == snapshot.capabilities[-1].manifest_digest
    assert changed.manifests[0].digest != snapshot.manifests[0].digest


def test_snapshot_and_resolution_are_deeply_immutable_and_detached():
    inputs = list(map(raw, graph()))
    disabled = ['org.gamma.tool']
    snapshot = build_dependency_snapshot(inputs, disabled=disabled)
    inputs.clear()
    disabled.clear()
    assert snapshot.disabled == frozenset({'org.gamma.tool'})
    result = snapshot.resolve('org.beta.tool')
    with pytest.raises(FrozenInstanceError):
        snapshot.digest = 'forged'
    with pytest.raises(FrozenInstanceError):
        result.capabilities[0].version = '2.0.0'
    with pytest.raises(AttributeError):
        result.capabilities[0].permissions.append('admin:all')
    with pytest.raises(AttributeError):
        snapshot.disabled.clear()
    assert snapshot.resolve('org.beta.tool') == result
    with pytest.raises(CapabilitySnapshotError, match='disabled'):
        snapshot.resolve('org.alpha.tool')
    # This is intentionally fixed evidence, not a live enablement feed.
    enabled = build_dependency_snapshot(list(map(raw, graph())))
    assert enabled.digest != snapshot.digest
    assert len(enabled.resolve('org.alpha.tool').capabilities) == 4


@pytest.mark.parametrize('disabled', ['org.alpha.tool', 'org.beta.tool', 'org.delta.tool'])
def test_disabled_root_or_transitive_dependency_denies_without_affecting_independent(disabled):
    snapshot = build_dependency_snapshot([*map(raw, graph()), raw(manifest('org.unrelated'))], disabled=[disabled])
    with pytest.raises(CapabilitySnapshotError, match='disabled'):
        snapshot.resolve('org.alpha.tool')
    assert len(snapshot.resolve('org.unrelated.tool').capabilities) == 1


@pytest.mark.parametrize(('version', 'constraint', 'accepted'), [
    ('1.4.0', '^1.0.0', True), ('2.0.0', '^1.0.0', False),
    ('1.1.0', '~1.0.0', False), ('1.0.4', '~1.0.0', True),
    ('0.2.9', '^0.2.0', True), ('0.3.0', '^0.2.0', False),
    ('0.0.1', '^0.0.1', True), ('0.0.2', '^0.0.1', False),
    ('1.0.0-rc.1', '^1.0.0', False), ('1.0.0-rc.1', '1.0.0', False),
])
def test_cross_namespace_version_rules_reuse_canonical_semver(version, constraint, accepted):
    inputs = [raw(manifest(dependencies=['org.beta.tool@' + constraint])),
              raw(manifest('org.beta', version=version))]
    if accepted:
        assert len(build_dependency_snapshot(inputs).resolve('org.alpha.tool').capabilities) == 2
    else:
        with pytest.raises(CapabilitySnapshotError, match='incompatible'):
            build_dependency_snapshot(inputs)


def test_missing_and_cyclic_graphs_fail_before_any_root_is_selected():
    with pytest.raises(CapabilitySnapshotError, match='missing'):
        build_dependency_snapshot([raw(manifest(dependencies=['org.absent.tool@1.0.0']))])
    a = manifest(dependencies=['org.beta.tool@1.0.0'])
    b = manifest('org.beta', dependencies=['org.alpha.tool@1.0.0'])
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot([raw(manifest('org.unrelated')), raw(a), raw(b)])
    self_cycle = manifest(dependencies=['org.alpha.tool@1.0.0'])
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot([raw(self_cycle)])


@pytest.mark.parametrize('other', ['org.alpha', 'org.alpha.child', 'org'])
def test_duplicate_and_overlapping_namespace_ownership_is_ambiguous(other):
    with pytest.raises(CapabilitySnapshotError, match='namespace'):
        build_dependency_snapshot([raw(manifest()), raw(manifest(other))])


@pytest.mark.parametrize('mutation', [
    lambda d: d.update(api_version='2'),
    lambda d: d.update(source='registered'),
    lambda d: d.update(extra='secret-do-not-echo'),
    lambda d: d['capabilities'][0].update(id='org.stolen.tool'),
    lambda d: d['capabilities'][0].update(version='01.0.0'),
    lambda d: d['capabilities'][0].update(kind='integration'),
    lambda d: d['capabilities'][0].update(runtime='shell'),
    lambda d: d['capabilities'][0].update(timeout_seconds=True),
    lambda d: d['capabilities'][0].update(timeout_seconds=301),
    lambda d: d['capabilities'][0].update(permissions=[123]),
    lambda d: d['capabilities'][0].update(permissions=['admin:all', 'admin:all']),
    lambda d: d['capabilities'][0].update(dependencies=['org.beta.tool@*']),
    lambda d: d['capabilities'][0].update(dependencies=['org.beta.tool@1.0.0-rc.1']),
    lambda d: d['capabilities'][0].update(dependencies=[None]),
    lambda d: d['capabilities'][0].update(dependencies=['org.beta.tool@1.0.0'] * 2),
    lambda d: d['capabilities'][0].update(handler='secret-do-not-echo'),
    lambda d: d['capabilities'].append(copy.deepcopy(d['capabilities'][0])),
])
def test_invalid_contracts_rejected_without_untrusted_values(mutation):
    data = manifest()
    mutation(data)
    with pytest.raises(CapabilitySnapshotError) as error:
        build_dependency_snapshot([raw(data)])
    assert 'secret-do-not-echo' not in str(error.value)
    assert 'org.' not in str(error.value)


@pytest.mark.parametrize('document', [b'[]', b'null', b'{}', b'\xff', b'{"id":1,"id":2}',
                                      b'{"value":NaN}', b'[' * 2000, b'not-json'])
def test_strict_bounded_json(document):
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot([document])


@pytest.mark.parametrize('inputs', [[], [b'{}'] * 33, (x for x in []), [bytearray(b'{}')],
                                    [b' ' * (snapshots.MAX_MANIFEST_BYTES + 1)]])
def test_byte_count_and_manifest_count_limits(inputs):
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot(inputs)


def test_total_byte_node_and_edge_limits(monkeypatch):
    inputs = [raw(manifest()), raw(manifest('org.beta'))]
    monkeypatch.setattr(snapshots, 'MAX_TOTAL_BYTES', len(inputs[0]))
    with pytest.raises(CapabilitySnapshotError, match='bytes'):
        build_dependency_snapshot(inputs)
    monkeypatch.setattr(snapshots, 'MAX_TOTAL_BYTES', 2 * 1024 * 1024)
    monkeypatch.setattr(snapshots, 'MAX_CAPABILITIES', 1)
    with pytest.raises(CapabilitySnapshotError, match='count'):
        build_dependency_snapshot(inputs)
    monkeypatch.setattr(snapshots, 'MAX_CAPABILITIES', 512)
    monkeypatch.setattr(snapshots, 'MAX_DEPENDENCIES', 1)
    with pytest.raises(CapabilitySnapshotError, match='count'):
        build_dependency_snapshot(list(map(raw, graph())))


@pytest.mark.parametrize('disabled', [['org.absent.tool'], [123], ['org.alpha.tool'] * 2,
                                      'org.alpha.tool', iter(['org.alpha.tool'])])
def test_disabled_input_is_bounded_exact_and_known(disabled):
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot([raw(manifest())], disabled=disabled)


@pytest.mark.parametrize(('root', 'require', 'message'), [
    ('org.absent.tool', '*', 'missing'), (None, '*', 'invalid'),
    ('org.alpha.tool', '^2.0.0', 'incompatible'), ('org.alpha.tool', '', 'invalid'),
    ('org.alpha.tool', True, 'invalid'),
])
def test_root_lookup_and_version_fail_safely(root, require, message):
    with pytest.raises(CapabilitySnapshotError, match=message):
        build_dependency_snapshot([raw(manifest())]).resolve(root, require=require)


def test_maximum_depth_uses_no_python_recursion_and_returns_dependencies_first():
    # Eight 64-entry manifests = the real 512-node bound. Cross-manifest edges
    # ensure local-only validation cannot accidentally make this test pass.
    manifests = []
    ids = [f'org.p{i // 64}.c{i}' for i in range(512)]
    for group in range(8):
        data = manifest(f'org.p{group}')
        data['capabilities'] = []
        for i in range(group * 64, (group + 1) * 64):
            item = manifest()['capabilities'][0]
            item.update(id=ids[i], dependencies=[ids[i + 1] + '@1.0.0'] if i < 511 else [])
            data['capabilities'].append(item)
        manifests.append(raw(data))
    snapshot = build_dependency_snapshot(manifests)
    assert [c.id for c in snapshot.resolve(ids[0]).capabilities] == ids[::-1]
    data = json.loads(manifests[-1])
    data['capabilities'][-1]['dependencies'] = [ids[0] + '@1.0.0']
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot([*manifests[:-1], raw(data)])


def test_no_network_process_registration_or_implicit_builtin_trust(monkeypatch):
    import socket
    import subprocess
    import urllib.request

    def forbidden(*args, **kwargs):
        pytest.fail('dependency resolution must be offline and inert')

    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    original = copy.deepcopy(load_registry().all())
    data = manifest()
    data['source'] = 'builtin'  # Caller labels are retained metadata, never trust.
    result = build_dependency_snapshot([raw(data)]).resolve('org.alpha.tool')
    assert result.capabilities[0].runtime == 'service'
    assert not hasattr(result, 'admission_granted')
    assert load_registry().all() == original
    # Existing execution registry must keep rejecting a missing dependency.
    data['capabilities'][0]['dependencies'] = ['org.absent.tool@1.0.0']
    with pytest.raises(ValueError, match='missing'):
        CapabilityRegistry(data)


@pytest.mark.parametrize('field,value', [
    ('dependencies', [f'org.other.c{i}@1.0.0' for i in range(65)]),
    ('permissions', [f'domain:scope{i}' for i in range(33)]),
    ('permissions', ['a' * 129 + ':read']),
])
def test_per_entry_limits(field, value):
    data = manifest()
    data['capabilities'][0][field] = value
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot([raw(data)])


@pytest.mark.parametrize('encoding', ['utf-16', 'utf-32'])
def test_only_utf8_manifest_bytes_are_accepted(encoding):
    with pytest.raises(CapabilitySnapshotError):
        build_dependency_snapshot([json.dumps(manifest()).encode(encoding)])


def test_disabled_order_has_no_effect_but_manifest_bytes_and_timeout_do():
    inputs = list(map(raw, graph()))
    disabled = ['org.beta.tool', 'org.gamma.tool']
    snapshot = build_dependency_snapshot(inputs, disabled=disabled)
    assert snapshot == build_dependency_snapshot(inputs, disabled=list(reversed(disabled)))
    changed = graph()
    changed[-1]['capabilities'][0]['timeout_seconds'] = 1
    assert snapshot.digest != build_dependency_snapshot(list(map(raw, changed)), disabled=disabled).digest
