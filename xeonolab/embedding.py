import hashlib
import json
import os
import threading
from pathlib import Path

from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, scope


class EmbedRequest(BaseModel):
    model: str = "fast"
    input: str | list[str]
    truncation: str = Field(default="reject", pattern="^(reject|truncate)$")


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

    @app.get("/v1/models")
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
                {"id": "Qwen/Qwen3-VL-Embedding-2B", "alias": "quality", "status": "not_configured"},
            ],
        }

    @app.post("/v1/embeddings")
    @app.post("/api/v1/embed/text")
    def embed(body: EmbedRequest, request: Request, actor=Depends(scope("embedding:write"))):
        if body.model not in ("fast", "D100M-Embedding", "quality", "Qwen/Qwen3-VL-Embedding-2B"):
            raise APIError(422, "UNKNOWN_MODEL", "unsupported embedding model")
        if body.model in ("quality", "Qwen/Qwen3-VL-Embedding-2B"):
            raise APIError(503, "MODEL_NOT_CONFIGURED", "Qwen worker not configured")
        if app.state.model is None:
            raise APIError(503, "MODEL_NOT_CONFIGURED", "D100M weights not configured")
        texts = [body.input] if isinstance(body.input, str) else body.input
        vectors, tokens = app.state.model.encode(texts, body.truncation)
        return {
            "object": "list",
            "model": "D100M-Embedding",
            "model_revision": app.state.model.revision,
            "data": [
                {"index": i, "object": "embedding", "embedding": vector} for i, vector in enumerate(vectors)
            ],
            "usage": {"input_tokens": tokens, "total_tokens": tokens},
        }

    return app
