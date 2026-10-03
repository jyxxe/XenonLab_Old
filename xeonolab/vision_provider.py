"""Thin HTTP client for the real embedding facade's shared Qwen image/text space."""

import hashlib
import io
import json
import math
import os
import ssl
import warnings
from typing import Literal

import httpx
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field, ValidationError

from .common import APIError
from .oauth import service_token

QWEN_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
IMAGE_MAX_BYTES = 16 * 1024 * 1024
IMAGE_MAX_PIXELS = 16_000_000


class VisionSpace(BaseModel):
    model_id: Literal["Qwen/Qwen3-VL-Embedding-2B"] = QWEN_MODEL
    model_revision: str = Field(min_length=1, max_length=256)
    dimensions: int = Field(strict=True, ge=1, le=2048)
    preprocessing_revision: str = Field(min_length=1, max_length=256)
    normalized: Literal[True] = True

    def key(self):
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()[:16]

    def collection(self):
        return "vision_" + self.key()

    def pins(self):
        return self.model_dump(exclude={"model_id", "normalized"})


def validate_image(payload, *, max_bytes=IMAGE_MAX_BYTES, max_pixels=IMAGE_MAX_PIXELS):
    """Validate original bytes; normalization belongs to the pinned model processor."""
    if len(payload) > max_bytes:
        raise APIError(413, "UPLOAD_TOO_LARGE", "16 MiB image limit")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(payload)) as image:
                if image.format not in {"PNG", "JPEG", "WEBP"}:
                    raise ValueError
                if image.width * image.height > max_pixels or getattr(image, "n_frames", 1) != 1:
                    raise ValueError
                media_type = Image.MIME[image.format]
                image.verify()
        return media_type
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise APIError(422, "INVALID_IMAGE", "supported single-frame PNG, JPEG or WEBP required") from None


class VisionProvider:
    def __init__(self, *, transport=None):
        endpoint = os.getenv("EMBEDDING_URL", "")
        if not endpoint:
            raise APIError(503, "MODEL_NOT_CONFIGURED", "embedding facade is not configured")
        verify = True
        ca_file = os.getenv("EMBEDDING_CA_FILE")
        if ca_file:
            try:
                verify = ssl.create_default_context(cafile=ca_file)
            except OSError:
                raise APIError(503, "MODEL_NOT_CONFIGURED", "embedding TLS trust is not configured") from None
        self.client = httpx.Client(
            base_url=endpoint.rstrip("/"), timeout=60, trust_env=False, verify=verify, transport=transport,
        )

    def close(self):
        self.client.close()

    def request(self, method, path, **kwargs):
        try:
            # Index jobs may outlive a service token; the OAuth helper refreshes
            # cached tokens before expiry, so resolve it for every facade request.
            with self.client.stream(
                method, path, headers={"Authorization": "Bearer " + service_token("EMBEDDING")}, **kwargs,
            ) as response:
                if response.status_code == 429:
                    raise APIError(429, "EMBEDDING_BUSY", "image embedding worker is busy", {"Retry-After": "1"})
                if response.status_code == 422:
                    raise APIError(422, "MODEL_SPACE_MISMATCH", "embedding input or space was rejected")
                if response.status_code == 413:
                    raise APIError(413, "UPLOAD_TOO_LARGE", "image exceeds embedding input limit")
                if response.status_code != 200:
                    raise APIError(503, "EMBEDDING_UNAVAILABLE", "image embedding service is unavailable")
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > 4 * 1024 * 1024:
                        raise ValueError
                    content.extend(chunk)
                result = json.loads(content)
                if not isinstance(result, dict):
                    raise ValueError
                return result
        except APIError:
            raise
        except (httpx.HTTPError, ValueError):
            raise APIError(503, "EMBEDDING_UNAVAILABLE", "image embedding request failed") from None

    def space(self):
        models = self.request("GET", "/v1/models").get("data")
        if not isinstance(models, list):
            raise APIError(503, "EMBEDDING_PROTOCOL_ERROR", "embedding model response is incompatible")
        matches = [model for model in models if isinstance(model, dict) and model.get("id") == QWEN_MODEL]
        if len(matches) > 1:
            raise APIError(503, "EMBEDDING_PROTOCOL_ERROR", "embedding model response is ambiguous")
        model = matches[0] if matches else None
        if not model or model.get("status") != "ready":
            raise APIError(503, "MODEL_NOT_CONFIGURED", "image embedding model is not ready")
        try:
            if model.get("normalized") is not True:
                raise ValueError
            for field, ceiling in (("max_batch_size", 32), ("max_tokens", 32768),
                                   ("image_max_bytes", IMAGE_MAX_BYTES), ("image_max_pixels", IMAGE_MAX_PIXELS)):
                value = model[field]
                if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= ceiling:
                    raise ValueError
            self.image_max_bytes = model["image_max_bytes"]
            self.image_max_pixels = model["image_max_pixels"]
            return VisionSpace(
                model_revision=model["model_revision"], dimensions=model["dimensions"],
                preprocessing_revision=model["preprocessing_revision"], normalized=model["normalized"],
            )
        except (KeyError, ValueError, ValidationError):
            raise APIError(503, "EMBEDDING_PROTOCOL_ERROR", "embedding model space is incomplete") from None

    def encode(self, space, *, payload=None, text=None, purpose="query"):
        if payload is not None:
            media = validate_image(payload, max_bytes=self.image_max_bytes, max_pixels=self.image_max_pixels)
            body = self.request(
                "POST", "/api/v1/embed/image", files={"upload": ("image", payload, media)},
                data={"model": "quality", "purpose": purpose, **{k: str(v) for k, v in space.pins().items()}},
            )
        else:
            body = self.request(
                "POST", "/v1/embeddings",
                json={"model": "quality", "input": [text], "purpose": purpose, **space.pins()},
            )
        try:
            if body["model"] != space.model_id or body.get("normalized") is not True:
                raise ValueError
            actual = VisionSpace(model_id=body["model"], **{k: body[k] for k in space.pins()}, normalized=True)
            if actual != space:
                raise ValueError
            data = body["data"]
            if not isinstance(data, list) or len(data) != 1 or data[0]["index"] != 0:
                raise ValueError
            vector = data[0]["embedding"]
            if not isinstance(vector, list) or len(vector) != space.dimensions:
                raise ValueError
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in vector):
                raise ValueError
            if not math.isclose(sum(x * x for x in vector), 1, rel_tol=1e-4, abs_tol=1e-4):
                raise ValueError
            return vector
        except (KeyError, TypeError, ValueError, ValidationError, OverflowError):
            raise APIError(503, "EMBEDDING_PROTOCOL_ERROR", "embedding vector space is incompatible") from None
