import copy
import logging
import time
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from xeonolab.capabilities import CapabilityRegistry, load_registry
from xeonolab.common import APIError, Identity
from xeonolab.embedding_providers import EmbeddingProviders, EmbeddingResult, ProviderBinding, ProviderState
from xeonolab.qwen_worker import QwenWorker
from xeonolab.services import create_app

from .conftest import auth
from .test_qwen_worker import SyntheticEncoder, config


class SyntheticProvider:
    def __init__(self, slot="fast", *, failure=None):
        self.slot = slot
        self.failure = failure
        self.started = self.closed = 0

    def start(self):
        self.started += 1
        if self.failure == "start":
            raise RuntimeError("SYNTHETIC_PRIVATE_DETAIL")

    def close(self):
        self.closed += 1
        if self.failure == "close":
            raise RuntimeError("SYNTHETIC_PRIVATE_DETAIL")

    def descriptor(self):
        if self.failure == "descriptor":
            raise APIError(500, "PRIVATE", "SYNTHETIC_PRIVATE_DETAIL")
        if self.failure == "invalid_descriptor":
            return {"id": None, "alias": self.slot, "status": "ready"}
        return {"id": "synthetic-" + self.slot, "alias": self.slot, "status": "ready", "dimensions": 2}

    def state(self):
        return ProviderState(True, False, "synthetic-revision", "ready")

    def prepare(self):
        return self

    def encode(self, texts, purpose, truncation, expected, image=None):
        if self.failure == "encode":
            raise RuntimeError("SYNTHETIC_PRIVATE_DETAIL")
        vectors = [[1.0, 0.0] for _ in texts]
        if self.failure == "invalid_vector":
            vectors = [[float("nan"), 0.0] for _ in texts]
        return EmbeddingResult("synthetic-" + self.slot,
                               {"model_revision": "synthetic-revision", "dimensions": 2, "normalized": True},
                               vectors, 7)


def binding(provider):
    quality = provider.slot == "quality"
    return ProviderBinding(provider.slot, (provider.slot, "Qwen/Qwen3-VL-Embedding-2B" if quality else "D100M-Embedding"),
                           "synthetic-" + provider.slot, "synthetic" + provider.slot,
                           "builtin.platform.provider.qwen-worker" if quality else "builtin.platform.provider.d100m",
                           "service" if quality else "api", provider, quality)


def gateway(settings):
    settings.rate_limit_enabled = False
    settings.dev_tokens["read"] = {"sub": "read", "scope": "embedding:read"}
    settings.dev_tokens["write"] = {"sub": "write", "scope": "embedding:write dev:read"}
    app = create_app("embedding", settings)
    return app


def manifest_with(identifier="builtin.platform.provider.d100m-encode", **overrides):
    manifest = copy.deepcopy(load_registry().all())
    for item in manifest["capabilities"]:
        if item["id"] == identifier:
            item.update(overrides)
    return CapabilityRegistry(manifest)


def test_startup_provider_replacement_preserves_callers_read_write_and_observability(settings):
    app = gateway(settings)
    provider = SyntheticProvider()
    app.state.providers.replace(binding(provider))
    assert app.state.model is None
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/v1/models", headers=auth("write")).status_code == 403
        assert client.get("/v1/models", headers=auth("read")).json()["data"][0]["id"] == "synthetic-fast"
        body = {"model": "fast", "input": ["one", "two"]}
        assert client.post("/v1/embeddings", headers=auth("read"), json=body).status_code == 403
        response = client.post("/v1/embeddings", headers=auth("write"), json=body)
        assert response.status_code == 200 and response.json()["model"] == "synthetic-fast"
        assert response.json()["usage"]["total_tokens"] == 7
        assert response.json()["dimensions"] == 2
        queue = client.get("/api/v1/embedding/queue", headers=auth("write")).json()["data"]
        assert queue["worker"] == {"configured": True, "busy": False, "revision": "synthetic-revision"}
        history = client.get("/api/v1/embedding/requests", headers=auth("write")).json()["data"]
        assert history[0]["model"] == "syntheticfast" and history[0]["caller"] == "write"
        assert 'embedding_model_loaded{model="syntheticfast"} 1' in client.get("/metrics").text
        with pytest.raises(ValueError, match="restart"):
            app.state.providers.replace(binding(SyntheticProvider()))
        app.state.providers.set_enabled("fast", False)
        assert len(client.get("/v1/models", headers=auth("read")).json()["data"]) == 1
        assert client.post("/v1/embeddings", headers=auth("write"), json=body).status_code == 404
        assert client.get("/health/ready").status_code == 503
        assert client.get("/api/v1/embedding/queue", headers=auth("write")).json()["data"]["counts"]["completed"] == 1
        app.state.providers.set_enabled("fast", True)
        assert client.post("/api/v1/embed/text", headers=auth("write"), json={**body, "model": "D100M-Embedding"}).status_code == 200
    assert provider.started == provider.closed == 1


@pytest.mark.parametrize("overrides,code", [
    ({"permissions": []}, "CAPABILITY_INVALID"), ({"kind": "widget"}, "CAPABILITY_INVALID"),
    ({"runtime": "ui"}, "CAPABILITY_INVALID"), ({"version": "2.0.0"}, "CAPABILITY_INCOMPATIBLE"),
    ({"permissions": ["embedding:write", "desktop:admin"]}, "FORBIDDEN"),
    ({"timeout_seconds": 1}, "CAPABILITY_INVALID"),
    ({"dependencies": ["builtin.platform.app.knowledge@1.0.0"]}, "CAPABILITY_DEPENDENCY_UNAVAILABLE"),
])
def test_encode_capability_boundaries(overrides, code):
    registry = EmbeddingProviders([binding(SyntheticProvider())], registry_loader=lambda: manifest_with(**overrides))
    with pytest.raises(APIError) as error:
        registry.prepare("fast", Identity("synthetic", {"embedding:write"}))
    assert error.value.code == code


def test_unknown_alias_missing_capability_and_descriptor_permissions():
    provider = binding(SyntheticProvider())
    registry = EmbeddingProviders([provider])
    with pytest.raises(APIError) as error:
        registry.prepare("missing", Identity("synthetic", {"embedding:write"}))
    assert error.value.code == "UNKNOWN_MODEL"
    with pytest.raises(APIError) as error:
        registry.prepare("fast", Identity("synthetic", {"embedding:read"}))
    assert error.value.status == 403
    restricted = EmbeddingProviders([provider], registry_loader=lambda: manifest_with(
        "builtin.platform.provider.d100m", permissions=["embedding:read", "desktop:admin"]))
    assert restricted.models(Identity("synthetic", {"embedding:read"})) == []
    missing = EmbeddingProviders([replace(provider, capability_id="plugin.unknown.provider")])
    with pytest.raises(APIError) as error:
        missing.prepare("fast", Identity("synthetic", {"embedding:write"}))
    assert error.value.status == 404
    with pytest.raises(ValueError):
        EmbeddingProviders([provider, replace(provider, slot="other")])


@pytest.mark.parametrize("failure", ["encode", "invalid_vector"])
def test_encoder_faults_never_record_success_or_leak_details(settings, failure):
    app = gateway(settings)
    app.state.providers.replace(binding(SyntheticProvider(failure=failure)))
    with TestClient(app) as client:
        response = client.post("/v1/embeddings", headers=auth("write"), json={"input": "sample"})
        assert response.status_code == 500 and response.json()["error"]["code"] == "INTERNAL_ERROR"
        assert "SYNTHETIC_PRIVATE_DETAIL" not in response.text
        history = client.get("/api/v1/embedding/requests", headers=auth("write")).json()["data"]
        assert history[0]["status"] == "failed" and history[0]["error_code"] == "INTERNAL_ERROR"
        assert 'embedding_errors_total{code="INTERNAL_ERROR",model="syntheticfast"} 1' in client.get("/metrics").text


def test_startup_and_shutdown_faults_are_isolated_and_owned_workers_close(settings, caplog):
    caplog.set_level(logging.WARNING, logger="xeonolab.embedding")
    app = gateway(settings)
    failed = SyntheticProvider(failure="start")
    healthy = SyntheticProvider("quality", failure="close")
    app.state.providers.replace(binding(failed))
    app.state.providers.replace(binding(healthy))
    with TestClient(app) as client:
        assert client.get("/v1/models", headers=auth("read")).json()["data"][0]["status"] == "unavailable"
        assert client.post("/v1/embeddings", headers=auth("write"), json={"model": "fast", "input": "x"}).status_code == 503
        assert client.post("/v1/embeddings", headers=auth("write"), json={"model": "quality", "input": "x"}).status_code == 200
    assert failed.closed == healthy.closed == 1
    assert "start_failed" in caplog.text and "close_failed" in caplog.text
    assert "SYNTHETIC_PRIVATE_DETAIL" not in caplog.text


@pytest.mark.parametrize("failure", ["descriptor", "invalid_descriptor"])
def test_bad_descriptors_do_not_break_other_model_discovery(settings, failure):
    app = gateway(settings)
    app.state.providers.replace(binding(SyntheticProvider(failure=failure)))
    with TestClient(app) as client:
        response = client.get("/api/v1/embed/models", headers=auth("read"))
        assert response.status_code == 200
        assert response.json()["data"][0]["status"] == "unavailable"
        assert response.json()["data"][1]["alias"] == "quality"
        assert "SYNTHETIC_PRIVATE_DETAIL" not in response.text


def test_qwen_process_deadline_is_retained_through_the_public_provider_route(settings, tmp_path):
    app = gateway(settings)
    app.state.quality = QwenWorker(config(tmp_path, timeout=.05), encoder_factory=SyntheticEncoder)
    with TestClient(app) as client:
        until = time.monotonic() + 5
        while app.state.quality.status == "loading" and time.monotonic() < until:
            time.sleep(.01)
        assert app.state.quality.status == "ready"
        response = client.post("/v1/embeddings", headers=auth("write"), json={"model": "quality", "input": "slow"})
        assert response.status_code == 503 and response.json()["error"]["code"] == "MODEL_TIMEOUT"
        assert not app.state.quality.process.is_alive()
        assert client.get("/api/v1/embedding/queue", headers=auth("write")).json()["data"]["counts"]["failed"] == 1


def test_provider_feature_flag_keeps_endpoint_scopes_and_validates_configuration(settings, monkeypatch):
    monkeypatch.setenv("EMBEDDING_CAPABILITY_ADAPTERS", "0")
    app = gateway(settings)
    app.state.providers.replace(binding(SyntheticProvider()))
    app.state.providers.set_enabled("fast", False)
    with TestClient(app) as client:
        assert client.post("/v1/embeddings", headers=auth("write"), json={"input": "x"}).status_code == 200
        assert client.post("/v1/embeddings", headers=auth("read"), json={"input": "x"}).status_code == 403
    monkeypatch.setenv("EMBEDDING_CAPABILITY_ADAPTERS", "bad")
    with pytest.raises(ValueError, match="EMBEDDING_CAPABILITY_ADAPTERS"):
        gateway(settings)


def test_registry_without_primary_has_honest_queue_state_and_other_provider_works(settings):
    app = gateway(settings)
    app.state.providers = EmbeddingProviders([binding(SyntheticProvider("quality"))])
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/api/v1/embedding/queue", headers=auth("write")).json()["data"]["worker"] == {
            "configured": False, "busy": False, "revision": None,
        }
        assert client.post("/v1/embeddings", headers=auth("write"), json={"model": "quality", "input": "x"}).status_code == 200


def test_registry_rejects_invalid_alias_bindings():
    for aliases in (("fast", "fast"), ("",), (None,), "fast"):
        with pytest.raises(ValueError):
            EmbeddingProviders([replace(binding(SyntheticProvider()), aliases=aliases)])
