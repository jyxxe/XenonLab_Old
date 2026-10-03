import hashlib
import json
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field

from .common import APIError, base_app, scope
from .qwen_encoder import IMAGE_MAX_BYTES, MODEL_ID, validate_image
from .qwen_worker import QwenWorker


class EmbedRequest(BaseModel):
    model: str = "fast"
    input: str | list[str]
    truncation: str = Field(default="reject", pattern="^(reject|truncate)$")
    purpose: str = Field(default="document", pattern="^(query|document)$")
    model_revision: str | None = Field(default=None, min_length=1, max_length=256)
    dimensions: int | None = Field(default=None, strict=True, ge=1, le=2048)
    preprocessing_revision: str | None = Field(default=None, min_length=1, max_length=256)


class ModelDescriptor(BaseModel):
    id: str
    alias: str
    status: str
    dimensions: int | None = None
    max_tokens: int | None = None
    normalized: bool | None = None
    revision: str | None = None
    model_revision: str | None = None
    preprocessing_revision: str | None = None
    max_batch_size: int | None = None
    image_max_bytes: int | None = None
    image_max_pixels: int | None = None
    image_formats: list[str] | None = None


class ModelsResponse(BaseModel):
    object: str = "list"
    data: list[ModelDescriptor]


class EmbeddingVector(BaseModel):
    index: int
    object: str = "embedding"
    embedding: list[float]


class EmbeddingUsage(BaseModel):
    input_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class EmbeddingsResponse(BaseModel):
    object: str = "list"
    model: str
    model_revision: str
    dimensions: int | None = None
    preprocessing_revision: str | None = None
    normalized: bool | None = None
    data: list[EmbeddingVector]
    usage: EmbeddingUsage


class EmbeddingError(BaseModel):
    code: str
    message: str
    details: dict[str, Any]
    request_id: str


class EmbeddingErrorResponse(BaseModel):
    error: EmbeddingError


ERROR_RESPONSES = {
    status: {"model": EmbeddingErrorResponse} for status in (400, 401, 403, 422, 429, 500, 503)
}


class D100M:
    def __init__(self, root):
        import torch
        from safetensors.torch import load_file

        from .vendor.mini_v4.config import load_model_config
        from .vendor.mini_v4.data.tokenizer import load_tokenizer
        from .vendor.mini_v4.model.embedding_model import MiniV4ForEmbedding

        self.torch = torch
        self.root = Path(root)
        config = json.loads((self.root / "embedding_config.json").read_text())
        if config["output_dim"] != 256 or config["max_length"] != 128 or not config["bidirectional"]:
            raise ValueError("incompatible D100M model configuration")
        torch.set_num_threads(int(os.getenv("EMBEDDING_CPU_THREADS", "4")))
        self.tokenizer = load_tokenizer(self.root / "tokenizer")
        self.model = MiniV4ForEmbedding(
            load_model_config(self.root / "model_config.yaml", self.root / "tokenizer"),
            config["output_dim"],
            config["pooling"],
            config["bidirectional"],
        )
        weights = self.root / "model.safetensors"
        self.model.load_state_dict(load_file(weights, device="cpu"))
        self.model.eval()
        digest = hashlib.sha256()
        for candidate in [
            weights,
            self.root / "embedding_config.json",
            self.root / "model_config.yaml",
            self.root / "tokenizer" / "tokenizer.json",
        ]:
            with candidate.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
        self.revision = digest.hexdigest()
        self.lock = threading.Lock()

    def encode(self, texts, truncation="reject"):
        if not 1 <= len(texts) <= 32 or any(not t.strip() or len(t) > 32768 for t in texts):
            raise APIError(422, "INVALID_INPUT", "1–32 nonempty texts required")
        encoded = [self.tokenizer.encode(text).ids for text in texts]
        if truncation == "reject" and any(len(ids) > 126 for ids in encoded):
            raise APIError(422, "TOKEN_LIMIT", "D100M allows 126 content tokens plus BOS/EOS")
        bos, eos, pad = [self.tokenizer.token_to_id(f"<|{name}|>") for name in ("bos", "eos", "pad")]
        encoded = [[bos, *ids[:126], eos] for ids in encoded]
        torch = self.torch
        ids = torch.full((len(texts), max(map(len, encoded))), pad, dtype=torch.long)
        mask = torch.zeros_like(ids, dtype=torch.bool)
        for i, sequence in enumerate(encoded):
            ids[i, : len(sequence)] = torch.tensor(sequence)
            mask[i, : len(sequence)] = True
        if not self.lock.acquire(blocking=False):
            raise APIError(429, "EMBEDDING_BUSY", "D100M worker busy; retry later")
        try:
            with torch.inference_mode():
                vectors = self.model(ids, mask).embeddings.tolist()
        finally:
            self.lock.release()
        return vectors, sum(map(len, encoded))

    def chunks(self, text, context="", target=108, overlap=16):
        context_ids = self.tokenizer.encode(context).ids[:18]
        tokens = self.tokenizer.encode(text).ids
        budget = min(target, 126 - len(context_ids))
        for start in range(0, len(tokens), budget - overlap):
            content = tokens[start : start + budget]
            yield self.tokenizer.decode(context_ids + content)
            if start + budget >= len(tokens):
                break


def create_app(settings=None):
    app = base_app("embedding", settings)
    root = os.getenv("D100M_MODEL_PATH")
    app.state.model = D100M(root) if root else None
    app.state.ready_check = lambda: app.state.model is not None
    app.state.quality = QwenWorker.environment()

    @asynccontextmanager
    async def lifespan(_app):
        app.state.quality.start()
        try:
            yield
        finally:
            app.state.quality.close()

    app.router.lifespan_context = lifespan

    @app.get(
        "/v1/models",
        response_model=ModelsResponse,
        response_model_exclude_unset=True,
        responses=ERROR_RESPONSES,
    )
    @app.get(
        "/api/v1/embed/models",
        response_model=ModelsResponse,
        response_model_exclude_unset=True,
        responses=ERROR_RESPONSES,
    )
    def models(actor=Depends(scope("embedding:read"))):
        model = app.state.model
        return {
            "object": "list",
            "data": [
                {
                    "id": "D100M-Embedding",
                    "alias": "fast",
                    "dimensions": 256,
                    "max_tokens": 128,
                    "normalized": True,
                    "status": "ready" if model else "not_configured",
                    "revision": model.revision if model else None,
                },
                app.state.quality.descriptor(),
            ],
        }

    @app.post(
        "/v1/embeddings",
        response_model=EmbeddingsResponse,
        response_model_exclude_unset=True,
        responses=ERROR_RESPONSES,
    )
    @app.post(
        "/api/v1/embed/text",
        response_model=EmbeddingsResponse,
        response_model_exclude_unset=True,
        responses=ERROR_RESPONSES,
    )
    def embed(body: EmbedRequest, request: Request, actor=Depends(scope("embedding:write"))):
        metrics = app.state.metrics
        quality = body.model in ("quality", "Qwen/Qwen3-VL-Embedding-2B")
        label = (
            "qwen3vl2b" if quality else ("d100m" if body.model in ("fast", "D100M-Embedding") else "unknown")
        )
        started = time.monotonic()

        def record_error(code):
            metrics.counter(
                "embedding_requests_total",
                help="Embedding requests by model and outcome.",
                model=label,
                result="error",
            )
            metrics.counter(
                "embedding_errors_total",
                help="Embedding errors by model and error code.",
                model=label,
                code=code,
            )

        try:
            if body.model not in ("fast", "D100M-Embedding", "quality", "Qwen/Qwen3-VL-Embedding-2B"):
                raise APIError(422, "UNKNOWN_MODEL", "unsupported embedding model")
            texts = [body.input] if isinstance(body.input, str) else body.input
            if quality:
                vectors, tokens = app.state.quality.encode(
                    texts,
                    body.purpose,
                    body.truncation,
                    expected={
                        "model_revision": body.model_revision,
                        "dimensions": body.dimensions,
                        "preprocessing_revision": body.preprocessing_revision,
                    },
                )
            else:
                if app.state.model is None:
                    raise APIError(503, "MODEL_NOT_CONFIGURED", "D100M weights not configured")
                vectors, tokens = app.state.model.encode(texts, body.truncation)
        except APIError as error:
            record_error(error.code)
            raise
        except Exception:
            record_error("INTERNAL_ERROR")
            raise
        finally:
            metrics.observe(
                "embedding_latency_seconds",
                time.monotonic() - started,
                help="Embedding request duration by model.",
                model=label,
            )
        metrics.counter(
            "embedding_requests_total",
            help="Embedding requests by model and outcome.",
            model=label,
            result="ok",
        )
        metrics.observe("embedding_batch_size", len(texts), help="Texts per embedding request.", model=label)
        space = app.state.quality.descriptor() if quality else {}
        return {
            "object": "list",
            "model": MODEL_ID if quality else "D100M-Embedding",
            "model_revision": space["model_revision"] if quality else app.state.model.revision,
            **(
                {key: space[key] for key in ("dimensions", "preprocessing_revision", "normalized")}
                if quality
                else {}
            ),
            "data": [
                {"index": i, "object": "embedding", "embedding": vector} for i, vector in enumerate(vectors)
            ],
            "usage": {"input_tokens": tokens, "total_tokens": tokens},
        }

    @app.post(
        "/api/v1/embed/image",
        response_model=EmbeddingsResponse,
        response_model_exclude_unset=True,
        responses=ERROR_RESPONSES,
    )
    def embed_image(
        request: Request,
        upload: UploadFile = File(...),
        model: str = Form("quality"),
        purpose: str = Form("document", pattern="^(query|document)$"),
        model_revision: str | None = Form(None, max_length=256),
        dimensions: int | None = Form(None, ge=1, le=2048),
        preprocessing_revision: str | None = Form(None, max_length=256),
        actor=Depends(scope("embedding:write")),
    ):
        metrics = app.state.metrics
        started = time.monotonic()
        try:
            if model not in ("quality", MODEL_ID):
                raise APIError(422, "UNKNOWN_MODEL", "image encoding requires the quality model")
            content = upload.file.read(IMAGE_MAX_BYTES + 1)
            validate_image(content)
            vectors, tokens = app.state.quality.encode(
                [""],
                purpose,
                image=content,
                expected={
                    "model_revision": model_revision,
                    "dimensions": dimensions,
                    "preprocessing_revision": preprocessing_revision,
                },
            )
        except APIError as error:
            metrics.counter(
                "embedding_requests_total",
                help="Embedding requests by model and outcome.",
                model="qwen3vl2b",
                result="error",
            )
            metrics.counter(
                "embedding_errors_total",
                help="Embedding errors by model and error code.",
                model="qwen3vl2b",
                code=error.code,
            )
            raise
        finally:
            upload.file.close()
            metrics.observe(
                "embedding_latency_seconds",
                time.monotonic() - started,
                help="Embedding request duration by model.",
                model="qwen3vl2b",
            )
        space = app.state.quality.descriptor()
        metrics.counter(
            "embedding_requests_total",
            help="Embedding requests by model and outcome.",
            model="qwen3vl2b",
            result="ok",
        )
        metrics.observe("embedding_batch_size", 1, help="Texts per embedding request.", model="qwen3vl2b")
        return {
            "object": "list",
            "model": MODEL_ID,
            **{
                key: space[key]
                for key in ("model_revision", "dimensions", "preprocessing_revision", "normalized")
            },
            "data": [{"index": 0, "object": "embedding", "embedding": vectors[0]}],
            "usage": {"input_tokens": tokens, "total_tokens": tokens},
        }

    metrics = app.state.metrics

    def model_loaded():
        model = app.state.model
        return [
            ({"model": "d100m"}, 1 if model is not None else 0),
            ({"model": "qwen3vl2b"}, int(app.state.quality.status == "ready")),
        ]

    def in_flight():
        lock = getattr(app.state.model, "lock", None)
        return [
            ({"model": "d100m"}, 1 if lock is not None and lock.locked() else 0),
            ({"model": "qwen3vl2b"}, int(app.state.quality.lock.locked())),
        ]

    def queue_depth():
        # V1 has no waiting queue: requests are rejected while the worker is busy.
        return [({"model": "d100m"}, 0), ({"model": "qwen3vl2b"}, 0)]

    metrics.gauge_provider(
        "embedding_worker_state",
        "Quality worker lifecycle state (one active state).",
        lambda: [({"model": "qwen3vl2b", "state": app.state.quality.status}, 1)],
    )
    metrics.gauge_provider(
        "embedding_model_loaded", "Loaded embedding models (1 loaded, 0 unavailable).", model_loaded
    )
    metrics.gauge_provider(
        "embedding_queue_depth",
        "Requests waiting for an embedding worker (always 0 in V1).",
        queue_depth,
    )
    metrics.gauge_provider(
        "embedding_in_flight",
        "Embedding requests currently using a worker.",
        in_flight,
    )

    return app
