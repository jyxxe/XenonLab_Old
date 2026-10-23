"""Compatibility is an admission gate, not a stripped version label."""
import copy
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from xeonolab.capabilities import SEMVER, CapabilityRegistry, satisfies
from xeonolab.services import create_app

from .conftest import auth
from .test_capabilities import manifest


@pytest.mark.parametrize('version,require,expected', [
    ('1.0.0-rc.1','1.0.0',False), ('1.0.0','1.0.0-rc.1',False),
    ('1.0.0-rc.1','1.0.0-rc.1',True), ('1.0.0-rc.2','1.0.0-rc.1',False),
    ('0.2.4','^0.2.3',True), ('0.3.0','^0.2.3',False), ('0.1.9','^0.2.3',False),
    ('0.0.4','^0.0.3',False), ('0.0.3','^0.0.3',True), ('0.0.1','^0.0.0',False),
    ('1.9.9','^1.2.3',True), ('2.0.0','^1.2.3',False),
    ('1.0.1-alpha','^1.0.0',False), ('1.0.0-rc.1','*',False), ('1.0.0','*',True),
    ('1.2.3-beta.11','^1.2.3-beta.2',True), ('1.2.3-beta.2','^1.2.3-beta.11',False),
    ('1.2.3','^1.2.3-beta.2',True), ('1.2.4-beta.3','^1.2.3-beta.2',False),
    ('1.2.4','^1.2.3-beta.2',True), ('1.2.3-alpha.1','~1.2.3-alpha',True),
    ('1.2.3-alpha','~1.2.3-alpha.1',False), ('1.2.3-1','~1.2.3-alpha',False),
    ('1.2.3-alpha','~1.2.3-1',True), ('1.2.3-alpha.2','~1.2.3-alpha.10',False),
    ('1.3.0','~1.2.3-alpha',False), ('1.2.4','~1.2.3',True),
    ('0.0.3-beta','^0.0.3-beta',True), ('0.0.4-beta','^0.0.3-beta',False),
    ('0.0.3','^0.0.3-beta',True), ('0.1.0','~0.0.3-beta',False),
])
def test_compatibility_does_not_confuse_release_identity_or_zero_major(version, require, expected):
    assert satisfies(version, require) is expected


@pytest.mark.parametrize('bad', ['', '1.2', '01.2.3', '1.02.3', '1.2.03', '1.2.3-',
    '1.2.3-.alpha', '1.2.3-alpha.', '1.2.3-alpha..1', '1.2.3-01', '1.2.3-alpha.01',
    '1.2.3+build', 'v1.2.3', '1.2.3\n', '１.2.3', '1.2.3_' , '9' * 257 + '.0.0'])
def test_invalid_versions_never_reach_comparison_or_manifest(bad):
    for require in (bad, '^' + bad, '~' + bad):
        with pytest.raises(ValueError):
            satisfies('1.2.3', require)
    with pytest.raises(ValueError):
        satisfies(bad, '*')
    for level in ('manifest', 'capability'):
        data = manifest()
        if level == 'manifest':
            data['version'] = bad
        else:
            data['capabilities'][0]['version'] = bad
        with pytest.raises(ValueError):
            CapabilityRegistry(data)


def test_schema_and_runtime_agree_on_canonical_version_and_stable_dependency_syntax():
    schema = json.loads((Path(__file__).resolve().parents[1] / 'infra/plugins/capability-manifest.schema.json').read_text())
    root = schema['properties']['version']
    item = schema['properties']['capabilities']['items']['properties']['version']
    assert root == item and root['pattern'] == SEMVER.pattern
    assert root['maxLength'] == 256
    for good in ('1.2.3', '0.0.0', '1.2.3-alpha.0', '1.2.3-001alpha', '1.2.3-a-b'):
        assert re.fullmatch(root['pattern'], good)
        assert satisfies(good, good)
    for bad in ('1.2.3-', '1.2.3-.', '1.2.3-01', '1.2.3-a..b'):
        assert not re.fullmatch(root['pattern'], bad)
    dep = schema['properties']['capabilities']['items']['properties']['dependencies']['items']['pattern']
    for good in ('builtin.test.api@1.2.3','builtin.test.api@^0.2.3','builtin.test.api@~1.2.3'):
        assert re.fullmatch(dep, good)
    for bad in ('builtin.test.api@^01.2.3','builtin.test.api@1.2','builtin.test.api@^1.0.0-'):
        assert not re.fullmatch(dep, bad)


def test_detail_api_returns_409_for_incompatible_but_422_for_invalid_constraints(settings, monkeypatch):
    settings.dev_tokens['alice']['scope'] += ' desktop:read'
    data = manifest()
    data['capabilities'][0]['version'] = '1.0.0-rc.1'
    registry = CapabilityRegistry(data)
    monkeypatch.setattr('xeonolab.capabilities.load_registry', lambda: registry)
    client = TestClient(create_app('desktop-web', settings))
    path = '/api/v1/capabilities/builtin.test.app.one'
    assert client.get(path, headers=auth(), params={'require':'1.0.0'}).status_code == 409
    assert client.get(path, headers=auth(), params={'require':'^1.0.0'}).status_code == 409
    assert client.get(path, headers=auth(), params={'require':'1.0.0-rc.1'}).status_code == 200
    invalid = client.get(path, headers=auth(), params={'require':'1.0.0-'})
    assert invalid.status_code == 422 and invalid.json()['error']['code'] == 'INVALID_VERSION_CONSTRAINT'
    assert client.get(path).status_code == 401
    data = copy.deepcopy(data)
    data['capabilities'][0]['permissions'] = ['xelibrary:admin']
    monkeypatch.setattr('xeonolab.capabilities.load_registry', lambda: CapabilityRegistry(data))
    assert client.get(path, headers=auth(), params={'require':'1.0.0-'}).status_code == 404


def test_published_schema_validates_real_manifests_and_does_not_require_empty_dependencies():
    from jsonschema import Draft202012Validator
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / 'infra/plugins/capability-manifest.schema.json').read_text())
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for path in (root / 'infra/plugins/builtin-capabilities.json', root / 'infra/plugins/capability-manifest.example.json'):
        validator.validate(json.loads(path.read_text()))
    data = manifest()
    other = copy.deepcopy(data['capabilities'][0])
    other['id'] = 'builtin.test.app.two'
    data['capabilities'].append(other)
    data['capabilities'][0]['dependencies'] = ['builtin.test.app.two@^1.0.0']
    validator.validate(data)
    CapabilityRegistry(data)
    for version in ('1.2.3', '0.0.0-0', '1.2.3-a.1', '1.2.3-01a', '1.2.3--'):
        data['version'] = version
        data['capabilities'][0]['version'] = version
        validator.validate(data)
        CapabilityRegistry(data)
    for bad in ('1.2.3\n','1.2.3\r\n','1.2.3-01','1.2.3-a..b','1.2.3-','1.2.3+build', '9'*257+'.0.0'):
        data['version'] = bad
        assert not validator.is_valid(data)
        with pytest.raises(ValueError):
            CapabilityRegistry(data)
    data['version'] = '1.0.0'
    data['capabilities'][0]['version'] = '1.0.0'
    for bad in ('builtin.test.app.two@^01.0.0','builtin.test.app.two@1.0.0\n'):
        data['capabilities'][0]['dependencies'] = [bad]
        assert not validator.is_valid(data)


@pytest.mark.parametrize('value', [None, 1, True, [], {}])
def test_nonstring_comparison_values_are_rejected(value):
    with pytest.raises(ValueError):
        satisfies(value, '*')
    with pytest.raises(ValueError):
        satisfies('1.0.0', value)


def test_docker_copy_layout_can_load_public_registry_and_version_gate(tmp_path):
    import shlex
    import shutil
    import subprocess
    import sys
    root = Path(__file__).resolve().parents[1]
    app = tmp_path / 'app'
    app.mkdir()
    for line in (root / 'infra/docker/Dockerfile').read_text().splitlines():
        fields = shlex.split(line)
        if not fields or fields[0] != 'COPY':
            continue
        assert len(fields) == 3
        source = root / fields[1]
        target = app / fields[2].removeprefix('./')
        if source.is_dir():
            shutil.copytree(source, target, ignore=shutil.ignore_patterns('__pycache__'))
        else:
            if fields[2].endswith('/'):
                target.mkdir(parents=True, exist_ok=True)
                target /= source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    code = '''import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import xeonolab.capabilities as capabilities
from xeonolab.capability_versions import satisfies
assert Path(capabilities.__file__).is_relative_to(Path(sys.argv[1]))
assert capabilities.load_registry().lookup("builtin.platform.worker.index")
assert not satisfies("1.0.0-rc.1", "1.0.0")
'''
    result = subprocess.run([sys.executable, '-I', '-c', code, str(app)],
                            cwd=tmp_path, env={}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
