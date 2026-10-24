import hashlib
import json
import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, File, Form, Request, Response, UploadFile
from pydantic import BaseModel, Field

from .common import APIError, base_app, response, scope
from .embedding_providers import D100MProvider, EmbeddingProviders, ProviderBinding, QwenProvider
from .embedding_requests import COMPLETED, FAILED, EmbeddingRequestStore
from .qwen_encoder import IMAGE_MAX_BYTES, MODEL_ID, validate_image
from .qwen_worker import QwenWorker

logger = logging.getLogger("xeonolab.embedding")



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
    status: {"model": EmbeddingErrorResponse} for status in (400, 401, 403, 404, 409, 422, 429, 500, 503)
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
    store = EmbeddingRequestStore(app.state.settings.data_dir / "embedding-requests.sqlite3")
    app.state.store = store

    def record(request_id, status, tokens=None, error_code=None):
        """Queue bookkeeping must never fail the embedding request itself."""
        if request_id is None:
            return
        try:
            store.finish(request_id, status, tokens=tokens, error_code=error_code)
        except Exception:
            logger.exception("embedding queue bookkeeping failed for request %s", request_id)

    @app.get("/api/v1/embedding/queue")
    def embedding_queue(request: Request, actor=Depends(scope("dev:read"))):
        """Caller-scoped unfinished requests plus totals for the desktop queue view
        (issue #74). History and counts only ever include the requesting principal;
        the worker block carries service-level state (configured/busy/revision)."""
        state = app.state.providers.primary_state()
        return response(
            request,
            {
                "active": store.active(caller=actor.sub),
                "counts": store.counts(caller=actor.sub),
                "worker": {
                    "configured": state.configured,
                    "busy": state.busy,
                    "revision": state.revision,
                },
            },
        )

    @app.get("/api/v1/embedding/requests")
    def embedding_requests(
        request: Request,
        limit: int = 100,
        cursor: str = "",
        status: str = "",
        actor=Depends(scope("dev:read")),
    ):
        """Newest-first request history for the calling principal; finished rows
        are retained up to 1000. Requests are never queued for later: a busy
        worker answers 429 directly and there is no waiting/priority scheduling."""
        if not 1 <= limit <= 1000:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        if status not in ("", "completed", "failed"):
            raise APIError(422, "INVALID_STATUS", "status must be completed or failed")
        try:
            rows, next_cursor = store.history(limit=limit, cursor=cursor, status=status or None, caller=actor.sub)
        except ValueError:
            raise APIError(422, "INVALID_CURSOR", "cursor must be a queue id") from None
        return response(request, rows, next_cursor=next_cursor)

    app.state.quality = QwenWorker.environment()
    provider_flag = os.getenv("EMBEDDING_CAPABILITY_ADAPTERS", "1")
    if provider_flag not in {"0", "1"}:
        raise ValueError("EMBEDDING_CAPABILITY_ADAPTERS must be 0 or 1")
    app.state.providers = EmbeddingProviders([
        ProviderBinding("fast", ("fast", "D100M-Embedding"), "D100M-Embedding", "d100m",
                        "builtin.platform.provider.d100m", "api", D100MProvider(lambda: app.state.model)),
        ProviderBinding("quality", ("quality", MODEL_ID), MODEL_ID, "qwen3vl2b",
                        "builtin.platform.provider.qwen-worker", "service", QwenProvider(lambda: app.state.quality), True, queue_label="quality"),
    ], enforce=provider_flag == "1")
    app.state.ready_check = lambda: app.state.providers.ready()

    def result_payload(result, count):
        try:
            payload = result.payload(count)
            EmbeddingsResponse.model_validate(payload)
            return payload
        except Exception:
            raise APIError(500, "INTERNAL_ERROR", "invalid embedding provider result") from None

    @asynccontextmanager
    async def lifespan(_app):
        app.state.providers.start()
        try:
            yield
        finally:
            app.state.providers.close()

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
    def models(response: Response, actor=Depends(scope("embedding:read"))):
        response.headers["Cache-Control"] = "no-store"
        return {"object": "list", "data": app.state.providers.models(actor, validate=ModelDescriptor.model_validate)}

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
        providers = app.state.providers
        label = providers.metric_label(body.model)
        started = time.monotonic()
        queue_id = None

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
            binding, call = providers.prepare(body.model, actor)
            label = binding.metric_label
            texts = [body.input] if isinstance(body.input, str) else body.input
            try:
                queue_id = store.start(
                    binding.history_label, len(texts), sum(map(len, texts)), body.truncation, actor.sub
                )
            except Exception:
                logger.exception("embedding queue bookkeeping failed")
                queue_id = None
            result = providers.encode(call, texts, body.purpose, body.truncation, {
                "model_revision": body.model_revision, "dimensions": body.dimensions,
                "preprocessing_revision": body.preprocessing_revision,
            })
            payload = result_payload(result, len(texts))
            record(queue_id, COMPLETED, tokens=result.tokens)
        except APIError as error:
            record(queue_id, FAILED, error_code=error.code)
            record_error(error.code)
            raise
        except Exception:
            record(queue_id, FAILED, error_code="INTERNAL_ERROR")
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
        return payload

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
        providers = app.state.providers
        label = providers.metric_label("quality")
        try:
            binding, call = providers.prepare(model, actor, image=True)
            label = binding.metric_label
            content = upload.file.read(IMAGE_MAX_BYTES + 1)
            validate_image(content)
            result = providers.encode(call, [""], purpose, "reject", {
                "model_revision": model_revision, "dimensions": dimensions,
                "preprocessing_revision": preprocessing_revision,
            }, image=content)
            payload = result_payload(result, 1)
        except APIError as error:
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
                code=error.code,
            )
            raise
        finally:
            upload.file.close()
            metrics.observe(
                "embedding_latency_seconds",
                time.monotonic() - started,
                help="Embedding request duration by model.",
                model=label,
            )
        metrics.counter(
            "embedding_requests_total", help="Embedding requests by model and outcome.", model=label, result="ok",
        )
        metrics.observe("embedding_batch_size", 1, help="Texts per embedding request.", model=label)
        return payload

    metrics = app.state.metrics

    def model_loaded():
        return [({"model": binding.metric_label}, int(state.configured)) for binding, state in app.state.providers.states()]

    def in_flight():
        return [({"model": binding.metric_label}, int(state.busy)) for binding, state in app.state.providers.states()]

    def queue_depth():
        return [({"model": binding.metric_label}, 0) for binding, _ in app.state.providers.states()]

    metrics.gauge_provider(
        "embedding_worker_state", "Quality worker lifecycle state (one active state).",
        lambda: [({"model": binding.metric_label, "state": state.status}, 1)
                 for binding, state in app.state.providers.states() if binding.slot == "quality"],
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
