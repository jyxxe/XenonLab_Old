"""Synthetic process/HTTP contracts: no Qwen weights or optional inference imports."""

import hashlib
import io
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from xeonolab.common import APIError
from xeonolab.embedding import create_app
from xeonolab.qwen_encoder import MODEL_ID, QwenConfig, QwenEncoder, validate_image, verify_snapshot
from xeonolab.qwen_worker import QwenWorker

from .conftest import auth


class SyntheticEncoder:
    def __init__(self, config):
        self.config = config
        assert os.getenv("HF_HUB_OFFLINE") == "1"
        assert os.getenv("TRANSFORMERS_OFFLINE") == "1"
        assert "EMBEDDING_SERVICE_TOKEN" not in os.environ

    def encode(self, texts, purpose, truncation="reject", image=None):
        if texts == ["slow"]:
            time.sleep(2)
        if texts == ["pause"]:
            time.sleep(0.4)
        if texts == ["reject"]:
            raise APIError(422, "TOKEN_LIMIT", "private-input-details")
        if texts == ["error"]:
            raise RuntimeError("private-model-details")
        if texts == ["crash"]:
            os._exit(1)
        return [[1.0, *([0.0] * (self.config.dimensions - 1))] for _ in texts], 7


class BrokenEncoder:
    def __init__(self, _config):
        raise RuntimeError("private-load-detail")


def config(tmp_path, **changes):
    return replace(
        QwenConfig(str(tmp_path), "a" * 40, str(tmp_path / "manifest.json"), dimensions=64), **changes
    )


def ready(worker):
    worker.start()
    deadline = time.monotonic() + 5
    while worker.status == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert worker.status == "ready"
    return worker


def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(stream, "PNG")
    return stream.getvalue()


def test_manifest_pins_identity_every_file_digest_and_full_snapshot(tmp_path):
    contents = {"config.json": b'{"model_type":"qwen3_vl"}', "model.safetensors": b"synthetic-not-weights"}
    for name, content in contents.items():
        (tmp_path / name).write_bytes(content)
    body = {
        "model_id": MODEL_ID,
        "model_revision": "a" * 40,
        "files": [
            {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in contents.items()
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(body))
    pinned = config(tmp_path)
    assert verify_snapshot(pinned) == tmp_path
    (tmp_path / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="size mismatch"):
        verify_snapshot(pinned)
    (tmp_path / "model.safetensors").write_bytes(contents["model.safetensors"])
    (tmp_path / "unexpected.json").write_text("{}")
    with pytest.raises(ValueError, match="complete"):
        verify_snapshot(pinned)
    (tmp_path / "unexpected.json").unlink()
    body["model_revision"] = "b" * 40
    (tmp_path / "manifest.json").write_text(json.dumps(body))
    with pytest.raises(ValueError, match="identity"):
        verify_snapshot(pinned)


def test_image_normal_decode_is_bounded_single_frame():
    validate_image(image_bytes())
    stream = io.BytesIO()
    Image.new("RGB", (8, 8), "green").save(
        stream, "GIF", save_all=True, append_images=[Image.new("RGB", (8, 8), "red")]
    )
    with pytest.raises(APIError) as error:
        validate_image(stream.getvalue())
    assert error.value.code == "INVALID_IMAGE"


def test_image_preprocessing_failure_propagates_instead_of_null_embedding(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    encoder = QwenEncoder.__new__(QwenEncoder)
    encoder.config = config(tmp_path)
    encoder.processor = SimpleNamespace(apply_chat_template=lambda *_a, **_k: ["synthetic-template"])

    def fail(*_a, **_k):
        raise RuntimeError("synthetic vision failure")

    monkeypatch.setitem(sys.modules, "qwen_vl_utils", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules, "qwen_vl_utils.vision_process", SimpleNamespace(process_vision_info=fail)
    )
    with pytest.raises(RuntimeError, match="synthetic vision failure"):
        encoder.encode([""], "query", image=image_bytes())


def test_real_optional_loader_fails_closed_without_local_snapshot(tmp_path):
    worker = QwenWorker(config(tmp_path))
    worker.start()
    deadline = time.monotonic() + 5
    while worker.status == "loading" and time.monotonic() < deadline:
        time.sleep(0.01)
    try:
        assert worker.status == "unavailable"
        with pytest.raises(APIError) as error:
            worker.encode(["hello"])
        assert error.value.code == "MODEL_UNAVAILABLE"
    finally:
        worker.close()
    assert not worker.process.is_alive()


def test_synthetic_spawn_busy_rejects_without_queue_and_reusable_input_error(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_SERVICE_TOKEN", "synthetic-must-not-reach-model")
    worker = ready(QwenWorker(config(tmp_path), encoder_factory=SyntheticEncoder))
    try:
        assert worker.descriptor()["preprocessing_revision"] == worker.config.preprocessing_revision
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(worker.encode, ["pause"], "query")
            deadline = time.monotonic() + 2
            while not worker.lock.locked() and time.monotonic() < deadline:
                time.sleep(0.005)
            with pytest.raises(APIError) as error:
                worker.encode(["second"])
            assert error.value.status == 429 and error.value.code == "EMBEDDING_BUSY"
            assert pending.result()[1] == 7
        with pytest.raises(APIError) as error:
            worker.encode(["reject"])
        assert error.value.status == 422
        assert "private" not in error.value.message
        assert worker.encode(["valid"])[1] == 7
        with pytest.raises(APIError) as error:
            worker.encode(["valid"], expected={"model_revision": "b" * 40})
        assert error.value.code == "MODEL_SPACE_MISMATCH"
    finally:
        worker.close()
    assert not worker.process.is_alive()


@pytest.mark.parametrize(
    "text,code,timeout",
    [
        ("slow", "MODEL_TIMEOUT", 0.1),
        ("crash", "MODEL_UNAVAILABLE", 2),
        ("error", "MODEL_UNAVAILABLE", 2),
    ],
)
def test_timeout_crash_failure_stop_process_before_releasing_gate(tmp_path, text, code, timeout):
    worker = ready(QwenWorker(config(tmp_path, timeout=timeout), encoder_factory=SyntheticEncoder))
    try:
        with pytest.raises(APIError) as error:
            worker.encode([text])
        assert error.value.code == code and "private" not in error.value.message
        assert not worker.process.is_alive() and not worker.lock.locked()
        assert worker.status == "unavailable"
        with pytest.raises(APIError) as error:
            worker.encode(["next"])
        assert error.value.status == 503
    finally:
        worker.close()


def test_synthetic_api_typed_text_image_scopes_metrics_and_fast_readiness(settings, tmp_path):
    settings.dev_tokens["reader"] = {"sub": "reader", "scope": "embedding:read"}
    settings.dev_tokens["writer"] = {"sub": "writer", "scope": "embedding:write"}
    app = create_app(settings)
    app.state.quality = QwenWorker(config(tmp_path), encoder_factory=SyntheticEncoder)
    with TestClient(app) as client:
        deadline = time.monotonic() + 5
        while app.state.quality.status == "loading" and time.monotonic() < deadline:
            time.sleep(0.01)
        row = client.get("/v1/models", headers=auth("reader")).json()["data"][1]
        assert row["status"] == "ready" and row["dimensions"] == 64
        assert client.get("/health/ready").status_code == 503  # D100M readiness remains independent.
        body = {
            "model": "quality",
            "input": ["hello"],
            "purpose": "query",
            **{key: row[key] for key in ("model_revision", "dimensions", "preprocessing_revision")},
        }
        assert client.post("/v1/embeddings", headers=auth("reader"), json=body).status_code == 403
        text = client.post("/api/v1/embed/text", headers=auth("writer"), json=body)
        assert text.status_code == 200, text.text
        result = text.json()
        assert result["model"] == MODEL_ID and result["normalized"] is True
        assert result["usage"] == {"input_tokens": 7, "total_tokens": 7}
        for key in ("model_revision", "dimensions", "preprocessing_revision"):
            assert result[key] == row[key]
        form = {key: str(value) for key, value in body.items() if key not in ("input",)}
        denied = client.post(
            "/api/v1/embed/image",
            headers=auth("reader"),
            data=form,
            files={"upload": ("image.png", image_bytes(), "image/png")},
        )
        assert denied.status_code == 403
        response = client.post(
            "/api/v1/embed/image",
            headers=auth("writer"),
            data=form,
            files={"upload": ("image.png", image_bytes(), "image/png")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["dimensions"] == row["dimensions"]
        assert response.json()["data"][0]["index"] == 0
        metrics = client.get("/metrics").text
        assert 'embedding_model_loaded{model="qwen3vl2b"} 1' in metrics
        assert 'embedding_requests_total{model="qwen3vl2b",result="ok"} 2' in metrics
        assert 'embedding_in_flight{model="qwen3vl2b"} 0' in metrics
        assert 'embedding_queue_depth{model="qwen3vl2b"} 0' in metrics
        assert 'embedding_worker_state{model="qwen3vl2b",state="ready"} 1' in metrics
        assert "EmbeddingsResponse" in app.openapi()["components"]["schemas"]
    assert not app.state.quality.process.is_alive()


class SlowLoadingEncoder:
    def __init__(self, _config):
        time.sleep(2)


def test_startup_deadline_and_close_are_terminal_idempotent(tmp_path):
    worker = QwenWorker(config(tmp_path, startup_timeout=0.05), encoder_factory=SlowLoadingEncoder)
    worker.start()
    time.sleep(0.1)
    assert worker.status == "unavailable"
    assert not worker.process.is_alive()
    worker.close()
    worker.close()
    worker.start()  # a terminal worker needs an application restart; no implicit reloading.
    assert worker.status == "unavailable" and not worker.process.is_alive()


def test_close_during_encode_invalidates_pending_request_and_leaves_no_child(tmp_path):
    worker = ready(QwenWorker(config(tmp_path), encoder_factory=SyntheticEncoder))
    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(worker.encode, ["pause"])
        deadline = time.monotonic() + 2
        while not worker.lock.locked() and time.monotonic() < deadline:
            time.sleep(0.005)
        worker.close()
        with pytest.raises(APIError) as error:
            pending.result(timeout=3)
        assert error.value.status == 503
    assert not worker.process.is_alive() and not worker.lock.locked()
    worker.close()


def test_bad_configuration_does_not_load_model_or_change_fast_readiness(settings, monkeypatch):
    monkeypatch.setenv("QWEN_MODEL_PATH", "/unused-synthetic-path")
    monkeypatch.delenv("QWEN_MODEL_REVISION", raising=False)
    app = create_app(settings)
    with TestClient(app) as client:
        assert app.state.quality.status == "unavailable"
        assert app.state.quality.process is None
        assert client.get("/health/ready").status_code == 503
        assert 'state="unavailable"' in client.get("/metrics").text


def test_launch_runtime_version_mismatch_fails_before_optional_model_import(tmp_path, monkeypatch):
    import xeonolab.qwen_encoder as module

    monkeypatch.setattr(module, "verify_snapshot", lambda _config: tmp_path)
    monkeypatch.setattr(module, "version", lambda _name: "synthetic-incompatible-version")
    with pytest.raises(ValueError, match="reviewed artifact"):
        QwenEncoder(config(tmp_path))


def test_empty_vision_preprocessing_cannot_return_successful_text_vector(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    encoder = QwenEncoder.__new__(QwenEncoder)
    encoder.config = config(tmp_path)
    encoder.processor = SimpleNamespace(apply_chat_template=lambda *_a, **_k: ["synthetic-template"])
    monkeypatch.setitem(sys.modules, "qwen_vl_utils", SimpleNamespace())
    monkeypatch.setitem(
        sys.modules,
        "qwen_vl_utils.vision_process",
        SimpleNamespace(process_vision_info=lambda *_a, **_k: (None, None)),
    )
    with pytest.raises(ValueError, match="preserve"):
        encoder.encode([""], "query", image=image_bytes())


def test_actual_common_error_envelope_matches_declared_models_on_all_embedding_routes(settings):
    from xeonolab.embedding import EmbeddingErrorResponse

    settings.dev_tokens["writer"] = {"sub": "writer", "scope": "embedding:write"}
    app = create_app(settings)
    with TestClient(app) as client:
        results = [
            client.get("/v1/models", headers=auth()),
            client.post(
                "/v1/embeddings", headers=auth("writer"), json={"model": "quality", "input": "hello"}
            ),
            client.post(
                "/api/v1/embed/text",
                headers=auth("writer"),
                json={"model": "quality", "input": "hello", "purpose": "invalid"},
            ),
            client.post(
                "/api/v1/embed/image",
                headers=auth("writer"),
                data={"dimensions": "invalid"},
                files={"upload": ("image.png", image_bytes(), "image/png")},
            ),
        ]
        assert [r.status_code for r in results] == [403, 503, 422, 422]
        for response in results:
            parsed = EmbeddingErrorResponse.model_validate(response.json())
            assert parsed.error.request_id == response.headers["X-Request-ID"]
        schema = app.openapi()
        for path, method in (
            ("/v1/models", "get"),
            ("/api/v1/embed/models", "get"),
            ("/v1/embeddings", "post"),
            ("/api/v1/embed/text", "post"),
            ("/api/v1/embed/image", "post"),
        ):
            for status in (403, 422, 429, 503):
                ref = schema["paths"][path][method]["responses"][str(status)]["content"]["application/json"][
                    "schema"
                ]
                assert ref["$ref"].endswith("EmbeddingErrorResponse")


def test_preprocessing_space_includes_effective_tokenizer_pin(tmp_path, monkeypatch):
    from xeonolab.qwen_encoder import RUNTIME_VERSIONS

    pinned = config(tmp_path)
    first = pinned.preprocessing_revision
    assert "-tk0.22.2-" in first
    monkeypatch.setitem(RUNTIME_VERSIONS, "tokenizers", "synthetic-other-tokenizer")
    assert pinned.preprocessing_revision != first
