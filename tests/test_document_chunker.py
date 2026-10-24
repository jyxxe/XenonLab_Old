import copy
import sqlite3
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from xeonolab.capabilities import CapabilityRegistry, load_registry
from xeonolab.common import APIError, Identity
from xeonolab.document_chunker import ChunkerBinding, DocumentChunkers
from xeonolab.services import Search, create_app

from .conftest import auth
from .test_index_status import create_table, make_client
from .test_qdrant_search import configure_qdrant, create_table_and_row
from .test_search import tiny_tokenizer, words

ADMIN = Identity("synthetic", {"xelibrary:admin"})


def one_chunk(tokenizer, source, path, name):
    yield "body", "alpha"


def manifest_with(**overrides):
    data = copy.deepcopy(load_registry().all())
    for item in data["capabilities"]:
        if item["id"] == "builtin.platform.worker.document-chunker":
            item.update(overrides)
    return CapabilityRegistry(data)


def test_registry_checks_scope_kind_runtime_version_and_enablement():
    binding = ChunkerBinding("example-v1", one_chunk)
    registry = DocumentChunkers(binding)
    assert registry.resolve(ADMIN).binding == binding
    with pytest.raises(APIError) as error:
        registry.resolve(Identity("reader", {"xelibrary:query", "xelibrary:read"}))
    assert error.value.status == 403
    registry.set_enabled(False)
    with pytest.raises(APIError) as error:
        registry.resolve(ADMIN)
    assert error.value.status == 404
    assert registry.identity == "example-v1", "reading stored data does not invoke a disabled processor"
    for overrides, code in [({"kind": "app"}, "CAPABILITY_INVALID"), ({"runtime": "service"}, "CAPABILITY_INVALID"),
                            ({"permissions": []}, "CAPABILITY_INVALID"), ({"version": "2.0.0"}, "CAPABILITY_INCOMPATIBLE"),
                            ({"dependencies": ["builtin.platform.app.knowledge@1.0.0"]}, "CAPABILITY_DEPENDENCY_UNAVAILABLE")]:
        invalid = DocumentChunkers(binding, registry_loader=lambda: manifest_with(**overrides))
        with pytest.raises(APIError) as error:
            invalid.resolve(ADMIN)
        assert error.value.code == code
    missing = DocumentChunkers(replace(binding, capability_id="plugin.unknown.chunker"))
    with pytest.raises(APIError) as error:
        missing.resolve(ADMIN)
    assert error.value.status == 404


def test_binding_identity_is_not_reused_or_replaced_after_processing():
    def other(*args):
        yield "body", "other"
    registry = DocumentChunkers(ChunkerBinding("default-v1", one_chunk))
    with pytest.raises(ValueError, match="new derived identity"):
        registry.replace(ChunkerBinding("default-v1", other))
    registry.replace(ChunkerBinding("other-v1", other))
    assert registry.identity == "other-v1"
    with pytest.raises(ValueError, match="restart"):
        registry.replace(ChunkerBinding("third-v1", one_chunk))
    for change in ({"identity": "../bad"}, {"max_chunks": True}, {"max_tokens": 127},
                   {"processing_seconds": float("nan")}, {"processor": None}):
        with pytest.raises(ValueError):
            DocumentChunkers(replace(ChunkerBinding("test-v1", one_chunk), **change))


def test_only_readonly_authorized_string_fields_and_context_reach_processor(tmp_path):
    _, tokenizer = tiny_tokenizer(tmp_path)
    observed = []
    def processor(tokenizer, source, path, name):
        observed.append((dict(source), path, name))
        with pytest.raises(TypeError):
            source["body"] = "changed"
        yield "body", source["body"]
    registry = DocumentChunkers(ChunkerBinding("readonly-v1", processor))
    chunks = list(registry.chunks(registry.resolve(ADMIN), tokenizer,
                                 {"body": "alpha", "empty": "", "blank": "  ", "number": 1, "nested": {}}, "/scope", "table"))
    assert chunks == [("body", "alpha")]
    assert observed == [({"body": "alpha"}, "/scope", "table")]


@pytest.mark.parametrize("item", [("hidden", "alpha"), ("body", ""), ("body", b"bytes"), ("body",),
                                 ("body", words(200))])
def test_invalid_output_is_rejected_before_embedding(tmp_path, item):
    _, tokenizer = tiny_tokenizer(tmp_path)
    registry = DocumentChunkers(ChunkerBinding("invalid-v1", lambda *args: iter([item])))
    with pytest.raises(APIError) as error:
        list(registry.chunks(registry.resolve(ADMIN), tokenizer, {"body": "alpha"}, "/", "t"))
    assert error.value.code == "CHUNKER_OUTPUT_INVALID"


def test_count_budget_and_processor_faults_are_safe(tmp_path):
    _, tokenizer = tiny_tokenizer(tmp_path)
    def infinite(*args):
        while True:
            yield "body", "alpha"
    registry = DocumentChunkers(ChunkerBinding("infinite-v1", infinite, max_chunks=2))
    with pytest.raises(APIError) as error:
        list(registry.chunks(registry.resolve(ADMIN), tokenizer, {"body": "alpha"}, "/", "t"))
    assert error.value.code == "CHUNKER_LIMIT"
    def broken(*args):
        raise RuntimeError("SYNTHETIC_PRIVATE_DETAIL")
    registry = DocumentChunkers(ChunkerBinding("broken-v1", broken))
    with pytest.raises(APIError) as error:
        list(registry.chunks(registry.resolve(ADMIN), tokenizer, {"body": "alpha"}, "/", "t"))
    assert error.value.code == "CHUNKER_FAILED" and "PRIVATE" not in error.value.message


@pytest.mark.parametrize("stage", ["factory", "iteration", "tail"])
def test_cooperative_budget_covers_factory_iteration_and_exhaustion(tmp_path, stage):
    _, tokenizer = tiny_tokenizer(tmp_path)
    now = [0.0]
    def factory(*args):
        if stage == "factory":
            now[0] += 2
            return iter([])
        def produce():
            if stage == "iteration":
                now[0] += 2
            yield "body", "alpha"
            now[0] += 2
        return produce()
    registry = DocumentChunkers(ChunkerBinding("timed-v1", factory, processing_seconds=1), clock=lambda: now[0])
    with pytest.raises(APIError) as error:
        list(registry.chunks(registry.resolve(ADMIN), tokenizer, {"body": "alpha"}, "/", "t"))
    assert error.value.code == "CHUNKER_TIMEOUT"


def reindex(client, tid, token="admin"):
    response = client.post("/api/v1/index/reindex", headers=auth(token), json={"table_id": tid})
    assert response.status_code == 202, response.text
    return client.get(f"/api/v1/jobs/{response.json()['data']['job_id']}", headers=auth(token)).json()["data"]


def index_rows(settings, tid):
    with sqlite3.connect(settings.data_dir / "tables" / f"{tid}.xl") as db:
        return db.execute("SELECT chunk_id,body,chunker_version FROM __xl_vectors ORDER BY chunk_id").fetchall()


def test_replacement_gates_old_derivatives_and_returns_only_current_source_snippets(settings, tmp_path, monkeypatch):
    settings.rate_limit_enabled = False
    old = make_client(settings, tmp_path, monkeypatch)
    tid = create_table(old)
    old.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"body": "original source fact"}})
    assert reindex(old, tid)["status"] == "completed"
    old_rows = index_rows(settings, tid)
    actor = Identity("alice", {"xelibrary:query", "xelibrary:read"})
    cached = old.app.state.semantic_collect(Search(query="fact"), actor)[0][0]
    current = TestClient(create_app("xelibrary", settings))
    calls = []
    def replacement(*args):
        calls.append(True)
        yield "body", "SYNTHETIC_DERIVATIVE_NOT_A_FACT"
    current.app.state.document_chunkers.replace(ChunkerBinding("replacement-v1", replacement))
    before = current.post("/api/v1/search/semantic", headers=auth(), json={"query": "fact"}).json()
    assert before["data"] == [] and before["meta"]["missing_embeddings"] == 1
    assert not current.app.state.search_revalidate(cached, Search(query="fact"), actor)
    assert reindex(current, tid)["status"] == "completed"
    new_rows = index_rows(settings, tid)
    assert {row[2] for row in new_rows} == {"replacement-v1"}
    assert {row[0] for row in new_rows}.isdisjoint({row[0] for row in old_rows})
    found = current.post("/api/v1/search/semantic", headers=auth(), json={"query": "fact"}).json()
    assert found["data"][0]["snippet"] == "original source fact"
    current.app.state.document_chunkers.set_enabled(False)
    assert current.post("/api/v1/index/reindex", headers=auth("admin"), json={"table_id": tid}).status_code == 404
    assert current.post("/api/v1/search/semantic", headers=auth(), json={"query": "fact"}).json()["data"]
    assert calls == [True], "cached reads never invoke the stopped processor"


def test_invalid_chunker_row_never_overwrites_existing_index(settings, tmp_path, monkeypatch):
    settings.rate_limit_enabled = False
    client = make_client(settings, tmp_path, monkeypatch)
    tid = create_table(client)
    client.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"body": "source fact"}})
    assert reindex(client, tid)["status"] == "completed"
    old_rows = index_rows(settings, tid)
    current = TestClient(create_app("xelibrary", settings))
    def bad(*args):
        yield "body", "alpha"
        yield "unauthorized_column", "private"
    current.app.state.document_chunkers.replace(ChunkerBinding("bad-v1", bad))
    job = reindex(current, tid)
    assert job["status"] == "failed" and "private" not in job["error"]
    assert index_rows(settings, tid) == old_rows


def test_qdrant_payload_and_point_ids_use_selected_identity(settings, tmp_path, monkeypatch):
    path, _ = tiny_tokenizer(tmp_path)
    monkeypatch.setenv("D100M_TOKENIZER_PATH", str(path))
    monkeypatch.setenv("EMBEDDING_URL", "http://embedding.invalid")
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "synthetic")
    state = configure_qdrant(tmp_path, monkeypatch)
    client = TestClient(create_app("xelibrary", settings))
    client.app.state.document_chunkers.replace(ChunkerBinding("qdrant-custom-v1", one_chunk))
    tid, rid = create_table_and_row(client)
    assert reindex(client, tid)["status"] == "completed"
    assert {point["payload"]["chunker_version"] for point in state["points"]} == {"qdrant-custom-v1"}
    first = {point["id"] for point in state["points"]}
    assert reindex(client, tid)["status"] == "completed"
    assert {point["id"] for point in state["points"]} == first
    found = client.post("/api/v1/search/semantic", headers=auth(), json={"query": "fact"}).json()
    assert found["data"][0]["row_id"] == rid and found["data"][0]["snippet"] == "fast text fact"


def test_feature_flag_and_admin_only_principal_do_not_gain_new_scopes(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("XELIBRARY_CHUNKER_CAPABILITIES", "0")
    settings.dev_tokens["only-admin"] = {"sub": "only-admin", "scope": "xelibrary:admin"}
    client = make_client(settings, tmp_path, monkeypatch)
    tid = create_table(client)
    client.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"body": "fact"}})
    client.app.state.document_chunkers.set_enabled(False)
    assert client.post("/api/v1/index/reindex", headers=auth(), json={"table_id": tid}).status_code == 403
    assert reindex(client, tid, "only-admin")["status"] == "completed"
    monkeypatch.setenv("XELIBRARY_CHUNKER_CAPABILITIES", "1")
    enabled = TestClient(create_app("xelibrary", settings))
    assert reindex(enabled, tid, "only-admin")["status"] == "completed"
    monkeypatch.setenv("XELIBRARY_CHUNKER_CAPABILITIES", "bad")
    with pytest.raises(ValueError, match="XELIBRARY_CHUNKER_CAPABILITIES"):
        create_app("xelibrary", settings)


def test_embedding_wait_is_not_charged_as_processor_time(tmp_path):
    _, tokenizer = tiny_tokenizer(tmp_path)
    now = [0.0]
    registry = DocumentChunkers(ChunkerBinding("steps-v1", lambda *args: iter([("body", "alpha"), ("body", "beta")])), clock=lambda: now[0])
    iterator = registry.chunks(registry.resolve(ADMIN), tokenizer, {"body": "source"}, "/", "t")
    assert next(iterator) == ("body", "alpha")
    now[0] += 1000  # caller's embedding request, outside processor execution
    assert next(iterator) == ("body", "beta")
    assert list(iterator) == []


def test_stopping_inflight_chunker_prevents_row_publication(settings, tmp_path, monkeypatch):
    client = make_client(settings, tmp_path, monkeypatch)
    tid = create_table(client)
    client.post(f"/api/v1/tables/{tid}/rows", headers=auth(), json={"values": {"body": "source"}})
    assert reindex(client, tid)["status"] == "completed"
    old_rows = index_rows(settings, tid)
    current = TestClient(create_app("xelibrary", settings))
    def stop_during_processing(*args):
        yield "body", "alpha"
        current.app.state.document_chunkers.set_enabled(False)
    current.app.state.document_chunkers.replace(ChunkerBinding("stopping-v1", stop_during_processing))
    assert reindex(current, tid)["status"] == "failed"
    assert index_rows(settings, tid) == old_rows
    assert current.get(f"/api/v1/tables/{tid}/rows", headers=auth()).status_code == 200


@pytest.mark.parametrize('enforce', [True, False])
def test_authority_denial_cannot_be_bypassed_by_local_flag(enforce, monkeypatch):
    class Actor:
        lifecycle_revision = 1
        def require(self, permission):
            assert permission == 'xelibrary:admin'
        def require_capability(self, identifier):
            if identifier == 'builtin.platform.worker.index':
                raise APIError(404, 'CAPABILITY_NOT_FOUND', 'disabled')
    registry = DocumentChunkers(ChunkerBinding('authority-v1', one_chunk), enforce=enforce)
    with pytest.raises(APIError) as error:
        registry.resolve(Actor())
    assert error.value.status == 404


def test_strict_mode_requires_live_checkpoint_and_verified_actor(monkeypatch):
    monkeypatch.setenv('CAPABILITY_LIFECYCLE_ENABLED', '1')
    registry = DocumentChunkers(ChunkerBinding('strict-v1', one_chunk), enforce=False)
    with pytest.raises(APIError) as error:
        registry.resolve(ADMIN)
    assert error.value.status == 503


def test_continuation_rechecks_declared_permissions_and_hides_context(tmp_path):
    state = {'allowed': True}
    class Actor:
        lifecycle_revision = 0
        def require_capability(self, identifier):
            pass
        def require(self, permission):
            if not state['allowed']:
                raise APIError(403, 'FORBIDDEN', 'revoked')
    actor = Actor()
    registry = DocumentChunkers(ChunkerBinding('live-v1', one_chunk))
    resolved = registry.resolve(actor, checkpoint=lambda: actor)
    assert 'checkpoint=' not in repr(resolved) and 'actor=' not in repr(resolved)
    state['allowed'] = False
    _, tokenizer = tiny_tokenizer(tmp_path)
    with pytest.raises(APIError) as error:
        list(registry.chunks(resolved, tokenizer, {'body':'alpha'}, '/', 'table'))
    assert error.value.status == 403


def test_publication_binding_check_never_calls_remote_checkpoint():
    def unexpected():
        raise AssertionError('authorization I/O inside publication transaction')
    registry = DocumentChunkers(ChunkerBinding('publication-v1', one_chunk))
    resolved = registry.resolve(ADMIN, checkpoint=unexpected)
    registry.check_binding(resolved)
    registry.set_enabled(False)
    with pytest.raises(APIError) as error:
        registry.check_binding(resolved)
    assert error.value.code == 'CHUNKER_CHANGED'
