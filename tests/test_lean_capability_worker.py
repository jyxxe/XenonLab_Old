"""Lean's advertised capability must gate actual synchronous and queued work."""

import copy
import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from xeonolab import capabilities
from xeonolab import lean_verifier as lean

CAPABILITY = 'builtin.platform.worker.lean-verifier'
SOURCE = 'theorem identity_proof : 1 = 1 := rfl\n'


@pytest.fixture
def setup_worker(settings, monkeypatch, tmp_path):
    settings.rate_limit_enabled = False
    settings.dev_tokens['alice']['scope'] += ' lean:verify'
    binary = tmp_path / 'approved-lean'
    binary.write_text('#!/bin/sh\nexit 0\n')
    binary.chmod(0o755)
    manifest = tmp_path / 'toolchains.json'
    manifest.write_text(json.dumps({'toolchains': [{'name': 'lean4', 'path': str(binary),
                                                   'sha256': hashlib.sha256(binary.read_bytes()).hexdigest()}]}))
    monkeypatch.setenv('LEAN_TOOLCHAINS_FILE', str(manifest))
    real_stat = os.stat
    namespaces = {f'/proc/{owner}/ns/{kind}': (1 if owner == 'self' else 2)
                  for owner in ('self', '1') for kind in ('net', 'mnt')}

    def synthetic_stat(path):
        name = str(path)
        if name in namespaces:
            return SimpleNamespace(st_uid=0, st_mode=stat.S_IFREG | 0o444,
                                   st_ino=namespaces[name], st_size=0)
        actual = real_stat(path)
        return SimpleNamespace(st_uid=0, st_mode=actual.st_mode, st_ino=actual.st_ino, st_size=actual.st_size)

    monkeypatch.setattr(lean, '_stat', synthetic_stat)
    calls = []

    def runner(toolchain, source, workdir, timeout, register=None, *, check_active=None):
        calls.append('executed')
        return {'status': 'verified', 'exit_code': 0, 'stdout': '', 'stderr': '', 'walltime_ms': 1,
                'axioms': [], 'dependency_lock_checked': False}

    return settings, calls, runner


@pytest.mark.parametrize('wait', [True, False])
def test_absent_registry_worker_cannot_be_invoked(setup_worker, monkeypatch, tmp_path, wait):
    settings, calls, runner = setup_worker
    manifest = copy.deepcopy(capabilities.load_registry().all())
    manifest['capabilities'] = [item for item in manifest['capabilities'] if item['id'] != CAPABILITY]
    monkeypatch.setattr(capabilities, 'load_registry', lambda: capabilities.CapabilityRegistry(manifest))
    api = TestClient(lean.create_app(settings, runner=runner))
    result = api.post('/api/v1/lean/verify', headers={'Authorization': 'Bearer alice'},
                      json={'source': SOURCE, 'wait': wait})
    assert result.status_code == 404, result.text
    assert calls == []
    with sqlite3.connect(settings.data_dir / 'lean-verifier.sqlite') as db:
        assert db.execute('SELECT count(*) FROM lean_jobs').fetchone()[0] == 0


def test_queued_owner_revocation_prevents_worker_start(setup_worker, monkeypatch):
    settings, calls, runner = setup_worker
    tasks = []

    class PausedPool:
        def __init__(self, *args, **kwargs):
            pass

        def submit(self, callback, *args):
            tasks.append((callback, args))

        def shutdown(self, *args, **kwargs):
            tasks.clear()

    monkeypatch.setattr(lean, 'ThreadPoolExecutor', PausedPool)
    api = TestClient(lean.create_app(settings, runner=runner))
    created = api.post('/api/v1/lean/verify', headers={'Authorization': 'Bearer alice'},
                       json={'source': SOURCE, 'wait': False})
    assert created.status_code == 200, created.text
    identifier = created.json()['data']['id']
    settings.dev_tokens['alice']['scope'] = 'desktop:read'
    callback, args = tasks.pop(0)
    callback(*args)
    assert calls == []
    with sqlite3.connect(settings.data_dir / 'lean-verifier.sqlite') as db:
        row = db.execute('SELECT status,error,result FROM lean_jobs WHERE id=?', (identifier,)).fetchone()
    assert row[0] == 'failed' and row[1] and not row[2]


def request(api, *, wait=True, source=SOURCE, timeout=10):
    return api.post('/api/v1/lean/verify', headers={'Authorization': 'Bearer alice'},
                    json={'source': source, 'wait': wait, 'timeout_seconds': timeout})


def job_record(settings, identifier):
    with sqlite3.connect(settings.data_dir / 'lean-verifier.sqlite') as db:
        return db.execute('SELECT status,error,result FROM lean_jobs WHERE id=?', (identifier,)).fetchone()


@pytest.mark.parametrize('field,value,status', [
    ('kind', 'provider', 503), ('runtime', 'api', 503), ('version', '2.0.0', 409),
    ('version', '1.0.0-rc.1', 409), ('permissions', [], 503),
    ('permissions', ['lean:verify', 'desktop:admin'], 403),
    ('dependencies', ['builtin.platform.worker.index@^1.0.0'], 503),
    ('timeout_seconds', 121, 503), ('timeout_seconds', True, 503), ('timeout_seconds', 1, 422),
])
def test_worker_contract_rejects_before_submission(setup_worker, monkeypatch, field, value, status):
    settings, calls, runner = setup_worker
    manifest = copy.deepcopy(capabilities.load_registry().all())
    item = next(c for c in manifest['capabilities'] if c['id'] == CAPABILITY)
    item[field] = value
    monkeypatch.setattr(capabilities, 'load_registry', lambda: capabilities.CapabilityRegistry(manifest))
    api = TestClient(lean.create_app(settings, runner=runner))
    result = request(api, wait=False)
    assert result.status_code == status, result.text
    assert calls == []
    with sqlite3.connect(settings.data_dir / 'lean-verifier.sqlite') as db:
        assert db.execute('SELECT count(*) FROM lean_jobs').fetchone()[0] == 0


@pytest.mark.parametrize('wait', [True, False])
def test_local_disable_denies_and_reenable_accepts_new_requests(setup_worker, wait):
    settings, calls, runner = setup_worker
    with TestClient(lean.create_app(settings, runner=runner)) as api:
        api.app.state.lean_workers.set_enabled(False)
        assert api.get('/health/ready').status_code == 503
        assert request(api, wait=wait).status_code == 404
        assert calls == []
        api.app.state.lean_workers.set_enabled(True)
        assert api.get('/health/ready').status_code == 200
        result = request(api, wait=wait)
        assert result.status_code == 200, result.text


@pytest.mark.parametrize('action', ['scope', 'subject', 'disabled', 'cycled', 'authority'])
def test_result_cannot_be_accepted_after_runner_changes_authority(setup_worker, monkeypatch, action):
    from xeonolab import common

    settings, calls, _ = setup_worker
    app = None

    def runner(toolchain, source, workdir, timeout, register=None, *, check_active=None):
        if action == 'scope':
            settings.dev_tokens['alice']['scope'] = 'desktop:read'
        elif action == 'subject':
            settings.dev_tokens['alice']['sub'] = 'replacement-principal'
        elif action in {'disabled', 'cycled'}:
            app.state.lean_workers.set_enabled(False)
            if action == 'cycled':
                app.state.lean_workers.set_enabled(True)
        else:
            def unavailable(request):
                raise RuntimeError('synthetic-private-detail')
            monkeypatch.setattr(common, 'refresh_identity', unavailable)
        # The checkpoint captures the refresh function once, so model a current
        # authority failure through the already bound resolver instead.
        if action == 'authority':
            app.state.authorization_resolver = lambda token: (_ for _ in ()).throw(RuntimeError('private-detail'))
        return {'status': 'verified', 'exit_code': 0, 'stdout': '', 'stderr': '', 'walltime_ms': 1,
                'axioms': [], 'dependency_lock_checked': False}

    app = lean.create_app(settings, runner=runner)
    api = TestClient(app)
    result = request(api)
    assert result.status_code in (401, 403, 404, 503), result.text
    assert 'private-detail' not in result.text


@pytest.mark.parametrize('mutation', [
    lambda value: value.update(status='pretend-success'),
    lambda value: value.update(exit_code=True),
    lambda value: value.update(exit_code=1),
    lambda value: value.update(dependency_lock_checked=True),
    lambda value: value.update(walltime_ms=-1),
    lambda value: value.update(walltime_ms=2**63),
    lambda value: value.update(stdout_truncated={'private': True}),
    lambda value: value.update(status='failed', exit_code=2**40),
    lambda value: value.update(stdout='x' * (lean.OUTPUT_LIMIT + 1)),
    lambda value: value.update(extra='synthetic-private-detail'),
])
def test_faulty_trusted_worker_results_fail_safely(setup_worker, mutation):
    settings, _, _ = setup_worker

    def runner(toolchain, source, workdir, timeout, register=None, *, check_active=None):
        result = {'status': 'verified', 'exit_code': 0, 'stdout': '', 'stderr': '', 'walltime_ms': 1,
                  'axioms': [], 'dependency_lock_checked': False}
        mutation(result)
        return result

    result = request(TestClient(lean.create_app(settings, runner=runner)))
    assert result.status_code == 503
    assert result.json()['error']['code'] == 'LEAN_WORKER_FAILED'
    assert 'synthetic-private-detail' not in result.text


def test_internal_refresh_does_not_charge_another_inbound_request(setup_worker, monkeypatch):
    from xeonolab import common

    settings, calls, runner = setup_worker
    charged = []
    original = common.enforce_rate_limit

    def counted(req, actor):
        charged.append(actor.sub)
        original(req, actor)

    monkeypatch.setattr(common, 'enforce_rate_limit', counted)
    api = TestClient(lean.create_app(settings, runner=runner))
    assert request(api).status_code == 200
    assert calls == ['executed'] and charged == ['alice']


@pytest.mark.parametrize('lifecycle', [None, True, -1])
def test_strict_lifecycle_does_not_fall_back_to_local_identity(setup_worker, monkeypatch, lifecycle):
    from xeonolab.common import Identity

    settings, calls, runner = setup_worker
    monkeypatch.setenv('CAPABILITY_LIFECYCLE_ENABLED', '1')
    actor = Identity('alice', {'lean:verify'}, 'codex')
    actor.lifecycle_revision = lifecycle
    actor.require_capability = lambda capability: None
    api = TestClient(lean.create_app(settings, runner=runner))
    api.app.state.authorization_resolver = lambda token: actor
    result = request(api)
    assert result.status_code == 503 and calls == []


def test_verified_lifecycle_cannot_disappear_on_a_later_refresh(setup_worker):
    from xeonolab.common import Identity

    settings, calls, runner = setup_worker
    actor = Identity('alice', {'lean:verify'}, 'codex')
    actor.lifecycle_revision = 0
    actor.require_capability = lambda capability: None
    count = [0]

    def resolve(token):
        count[0] += 1
        return actor if count[0] == 1 else Identity('alice', {'lean:verify'}, 'codex')

    api = TestClient(lean.create_app(settings, runner=runner))
    api.app.state.authorization_resolver = resolve
    assert request(api).status_code == 503 and calls == []


def test_restart_preserves_history_but_never_reexecutes_old_work(setup_worker, monkeypatch):
    settings, calls, runner = setup_worker
    tasks = []

    class PausedPool:
        def __init__(self, *args, **kwargs):
            pass

        def submit(self, callback, *args):
            tasks.append((callback, args))

        def shutdown(self, *args, **kwargs):
            tasks.clear()

    monkeypatch.setattr(lean, 'ThreadPoolExecutor', PausedPool)
    api = TestClient(lean.create_app(settings, runner=runner))
    created = request(api, wait=False)
    identifier = created.json()['data']['id']
    settings.dev_tokens['alice']['scope'] = 'desktop:read'
    with TestClient(lean.create_app(settings, runner=runner)):
        assert not calls
        assert job_record(settings, identifier) == ('failed', 'LEAN_REAUTHORIZATION_REQUIRED', '')
    assert list((settings.data_dir / 'lean-jobs').rglob('source.lean'))
    assert job_record(settings, identifier)[0] == 'failed'


def test_bearer_is_never_serialized_to_job_or_result(setup_worker):
    from uuid import uuid4

    settings, _, runner = setup_worker
    token = 'synthetic-credential-' + uuid4().hex
    settings.dev_tokens[token] = dict(settings.dev_tokens['alice'])
    with TestClient(lean.create_app(settings, runner=runner)) as api:
        result = api.post('/api/v1/lean/verify', headers={'Authorization': 'Bearer ' + token},
                          json={'source': SOURCE, 'wait': False, 'timeout_seconds': 2})
        assert result.status_code == 200
    for path in settings.data_dir.rglob('*'):
        if path.is_file():
            assert token.encode() not in path.read_bytes(), path.name


def test_native_walltime_independent_of_blocking_authority_callback(setup_worker, tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from .test_lean_verifier import FAKE_LEAN, write_toolchain

    write_toolchain(tmp_path, FAKE_LEAN)
    toolchain = lean.load_toolchains(os.environ['LEAN_TOOLCHAINS_FILE'])['lean4']
    source = tmp_path / 'blocking.lean'
    source.write_text(SOURCE + ' lean-marker: hang')
    entered, release, registered = threading.Event(), threading.Event(), threading.Event()
    state = {'calls': 0}

    def active():
        state['calls'] += 1
        if state['calls'] > 1:
            entered.set()
            assert release.wait(5)

    def register(process):
        if process is not None:
            state['process'] = process
            registered.set()

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lean.default_runner, toolchain, source, tmp_path, 1, register, check_active=active)
        try:
            assert registered.wait(2) and entered.wait(2)
            # Authorization is still blocked; the process watchdog must finish.
            assert state['process'].wait(timeout=2) < 0
        finally:
            release.set()
        result = future.result(timeout=5)
    assert result['status'] == 'timeout'


@pytest.mark.parametrize('change', ['disabled', 'revoked', 'cycled', 'authority'])
def test_native_running_job_is_killed_and_no_result_accepted(setup_worker, monkeypatch, tmp_path, change):
    import time

    from .test_lean_verifier import FAKE_LEAN, write_toolchain

    settings, _, _ = setup_worker
    marker = tmp_path / 'started-pid'
    body = FAKE_LEAN.replace('    time.sleep(30)',
                             f"    open({str(marker)!r}, 'w').write(str(os.getpid()))\n    time.sleep(30)")
    write_toolchain(tmp_path, body)
    api = TestClient(lean.create_app(settings))
    workers = api.app.state.lean_workers
    original = workers.checkpoint

    def fast_checkpoint(req, actor):
        checkpoint = original(req, actor)
        checkpoint._authorization._interval = 0.05
        return checkpoint

    monkeypatch.setattr(workers, 'checkpoint', fast_checkpoint)
    created = request(api, wait=False, source=SOURCE + ' lean-marker: hang', timeout=10)
    identifier = created.json()['data']['id']
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if marker.exists():
            break
        time.sleep(0.02)
    assert marker.exists(), 'test must observe a real child before changing authorization'
    pid = int(marker.read_text())
    if change in {'disabled', 'cycled'}:
        workers.set_enabled(False)
        if change == 'cycled':
            workers.set_enabled(True)
    elif change == 'authority':
        api.app.state.authorization_resolver = lambda token: (_ for _ in ()).throw(RuntimeError('private-detail'))
    else:
        settings.dev_tokens['alice']['scope'] = 'desktop:read'
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and job_record(settings, identifier)[0] in ('queued', 'running'):
        time.sleep(0.02)
    record = job_record(settings, identifier)
    assert record[0] == 'failed' and record[2] == ''
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.parametrize('wait', [True, False])
def test_storage_failure_releases_capacity_and_source_directory(setup_worker, monkeypatch, wait):
    settings, _, runner = setup_worker
    monkeypatch.setattr(lean, 'MAX_CONCURRENT', 1)
    original = Path.write_text
    fail = [True]

    def write(path, *args, **kwargs):
        if path.name == 'source.lean' and fail[0]:
            raise OSError('synthetic-private-storage-detail')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'write_text', write)
    with TestClient(lean.create_app(settings, runner=runner)) as api:
        result = request(api, wait=wait)
        assert result.status_code == 503 and 'private-storage' not in result.text
        assert not list((settings.data_dir / 'lean-jobs').iterdir())
        fail[0] = False
        assert request(api).status_code == 200


def test_queue_limit_is_atomic_when_two_preflight_counts_are_stale(setup_worker, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    settings, _, runner = setup_worker
    monkeypatch.setattr(lean, 'MAX_QUEUED', 1)

    class PausedPool:
        def __init__(self, *args, **kwargs):
            pass

        def submit(self, *args, **kwargs):
            pass

        def shutdown(self, *args, **kwargs):
            pass

    monkeypatch.setattr(lean, 'ThreadPoolExecutor', PausedPool)
    original = Path.write_text
    barrier = threading.Barrier(2)

    def write(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path.name == 'source.lean':
            barrier.wait(timeout=5)  # Both requests already observed an empty queue.
        return result

    monkeypatch.setattr(Path, 'write_text', write)
    with TestClient(lean.create_app(settings, runner=runner)) as api:
        with ThreadPoolExecutor(max_workers=2) as clients:
            results = list(clients.map(lambda _: request(api, wait=False), range(2)))
        assert sorted(result.status_code for result in results) == [200, 429]
        with sqlite3.connect(settings.data_dir / 'lean-verifier.sqlite') as db:
            assert db.execute('SELECT count(*) FROM lean_jobs').fetchone()[0] == 1
        assert len(list((settings.data_dir / 'lean-jobs').rglob('source.lean'))) == 1


def test_authority_rate_limit_is_not_ignored_by_internal_refresh(setup_worker, monkeypatch):
    import ssl

    import httpx

    from xeonolab import common

    settings, calls, runner = setup_worker
    count = [0]

    class AuthorityClient:
        def __init__(self, **kwargs):
            assert isinstance(kwargs['verify'], ssl.SSLContext)
            assert kwargs['trust_env'] is False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers):
            count[0] += 1
            if count[0] > 1:
                return httpx.Response(429, request=httpx.Request('GET', url), headers={'Retry-After': '7'})
            return httpx.Response(200, request=httpx.Request('GET', url),
                                  json={'data': {'subject': 'alice', 'kind': 'codex', 'scopes': ['lean:verify']}})

    monkeypatch.setenv('AUTHORIZATION_URL', 'https://authority.example/api/v1/auth/authorization')
    monkeypatch.setattr(common.httpx, 'Client', AuthorityClient)
    api = TestClient(lean.create_app(settings, runner=runner))
    result = request(api)
    assert result.status_code == 429 and result.headers['Retry-After'] == '7'
    assert calls == []


def test_replacement_worker_keeps_kernel_owned_result_annotations(setup_worker):
    settings, _, _ = setup_worker

    def replacement(toolchain, source, workdir, timeout, register=None, *, check_active=None):
        assert callable(check_active) and check_active() is replacement
        assert timeout == 10
        return {'status': 'verified', 'exit_code': 0, 'stdout': "'target' depends on axioms: [printedAxiom]",
                'stderr': '', 'walltime_ms': 1, 'axioms': ['fabricatedAxiom'], 'dependency_lock_checked': False}

    result = request(TestClient(lean.create_app(settings, runner=replacement)),
                      source=SOURCE + 'axiom declaredAxiom : False\n')
    assert result.status_code == 200
    assert result.json()['data']['axioms'] == ['declaredAxiom', 'printedAxiom']


def test_watchdog_start_failure_kills_the_child_before_reporting_worker_failure(setup_worker, monkeypatch, tmp_path):
    from .test_lean_verifier import FAKE_LEAN, write_toolchain

    settings, _, _ = setup_worker
    write_toolchain(tmp_path, FAKE_LEAN)
    processes = []
    original = lean.subprocess.Popen

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    class FailedTimer:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError('private-watchdog-error')

        def cancel(self):
            pass

    with TestClient(lean.create_app(settings)) as api:
        monkeypatch.setattr(lean.subprocess, 'Popen', spawn)
        monkeypatch.setattr(lean.threading, 'Timer', FailedTimer)
        result = request(api, source=SOURCE + ' lean-marker: hang')
        assert result.status_code == 503 and 'private-watchdog' not in result.text
        assert len(processes) == 1 and processes[0].poll() < 0


def test_production_signed_jwt_is_not_a_live_job_authorization(setup_worker, monkeypatch):
    import time

    import jwt
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    settings, calls, runner = setup_worker
    monkeypatch.delenv('AUTHORIZATION_URL', raising=False)
    settings.mode = 'production'
    settings.issuer = 'https://issuer.example/realm'
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings.public_key = key.public_key().public_bytes(serialization.Encoding.PEM,
                                                       serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    token = jwt.encode({'sub': 'alice', 'scope': 'lean:verify', 'agent_kind': 'codex',
                        'iss': settings.issuer, 'aud': settings.audience,
                        'iat': int(time.time()), 'exp': int(time.time()) + 60}, key, algorithm='RS256')
    api = TestClient(lean.create_app(settings, runner=runner))
    result = api.post('/api/v1/lean/verify', headers={'Authorization': 'Bearer ' + token},
                      json={'source': SOURCE})
    assert result.status_code == 503
    assert result.json()['error']['code'] == 'AUTHORIZATION_NOT_CONFIGURED'
    assert api.get('/health/ready').status_code == 503 and calls == []
    with sqlite3.connect(settings.data_dir / 'lean-verifier.sqlite') as db:
        db.execute("INSERT INTO lean_jobs(id,owner,toolchain,timeout,source_path,status,result,created,updated) "
                   "VALUES('old-proof','alice','lean4',1,'/unused-local-fixture','verified','{}',1,1)")
    headers = {'Authorization': 'Bearer ' + token}
    assert api.get('/api/v1/lean/jobs/old-proof', headers=headers).status_code == 503
    assert api.post('/api/v1/lean/jobs/old-proof/cancel', headers=headers).status_code == 503


@pytest.mark.parametrize('endpoint', ['http://authority.example/api/v1/auth/authorization',
                                     'https://user:private@authority.example/path',
                                     'https://authority.example/path?token=private'])
def test_production_refuses_insecure_or_credential_bearing_authority_config(setup_worker, monkeypatch, endpoint):
    settings, calls, runner = setup_worker
    settings.mode = 'production'
    monkeypatch.setenv('AUTHORIZATION_URL', endpoint)
    with pytest.raises(ValueError) as error:
        lean.create_app(settings, runner=runner)
    assert 'private' not in str(error.value) and calls == []


@pytest.mark.parametrize('subject', [None, ''])
def test_live_job_identity_must_have_a_subject(setup_worker, subject):
    from xeonolab.common import Identity

    settings, calls, runner = setup_worker
    api = TestClient(lean.create_app(settings, runner=runner))
    api.app.state.authorization_resolver = lambda token: Identity(subject, {'lean:verify'}, 'codex')
    assert request(api).status_code == 503 and calls == []


def test_output_reader_start_failure_kills_and_reaps_native_child(setup_worker, monkeypatch, tmp_path):
    from .test_lean_verifier import FAKE_LEAN, write_toolchain

    write_toolchain(tmp_path, FAKE_LEAN)
    toolchain = lean.load_toolchains(os.environ['LEAN_TOOLCHAINS_FILE'])['lean4']
    source = tmp_path / 'reader-failure.lean'
    source.write_text(SOURCE + ' lean-marker: hang')
    processes = []
    original = lean.subprocess.Popen
    real_threads = lean.threading

    def spawn(*args, **kwargs):
        process = original(*args, **kwargs)
        processes.append(process)
        return process

    class FailedReader:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            raise RuntimeError('reader-start-failed')

    monkeypatch.setattr(lean.subprocess, 'Popen', spawn)
    # Replace this module's view only, retaining a real independent Timer.
    monkeypatch.setattr(lean, 'threading', SimpleNamespace(Event=real_threads.Event,
                                                         Timer=real_threads.Timer, Thread=FailedReader))
    with pytest.raises(RuntimeError, match='reader-start-failed'):
        lean.default_runner(toolchain, source, tmp_path, 10)
    assert len(processes) == 1 and processes[0].poll() < 0
    assert processes[0].stdout.closed and processes[0].stderr.closed
