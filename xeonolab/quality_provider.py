"""Quality encoding stays behind the authenticated embedding service API."""

import json
import math
import os
import ssl
import time
from dataclasses import dataclass

import httpx

from .common import APIError
from .oauth import service_token

QUALITY_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
MAX_RESPONSE_BYTES = 4 * 1024 * 1024


def protocol_error():
    return APIError(503, "QWEN_PROTOCOL_ERROR", "quality worker returned an invalid response")


def _text(value, maximum=256):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise protocol_error()
    return value


def _integer(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise protocol_error()
    return value


@dataclass(frozen=True)
class QualityDescriptor:
    model_id: str
    model_revision: str
    dimensions: int
    preprocessing_revision: str
    max_batch_size: int
    max_tokens: int
    normalized: bool

    @property
    def space_key(self):
        return self.model_id, self.model_revision, self.dimensions, self.preprocessing_revision

    @classmethod
    def from_model(cls, row):
        if not isinstance(row, dict) or row.get("id") != QUALITY_MODEL or row.get("alias") != "quality":
            raise protocol_error()
        if row.get("status") != "ready":
            raise APIError(503, "QWEN_UNAVAILABLE", "quality worker is not configured or ready")
        if not isinstance(row.get("normalized"), bool):
            raise protocol_error()
        return cls(
            model_id=QUALITY_MODEL,
            model_revision=_text(row.get("model_revision")),
            dimensions=_integer(row.get("dimensions"), 1, 2048),
            preprocessing_revision=_text(row.get("preprocessing_revision")),
            max_batch_size=_integer(row.get("max_batch_size"), 1, 32),
            max_tokens=_integer(row.get("max_tokens"), 1, 32768),
            normalized=row["normalized"],
        )


class QualityClient:
    """Bounded HTTP client; it never substitutes D100M or generated vectors."""

    def __init__(self, endpoint=None, *, token_provider=None, transport=None):
        self.endpoint = (endpoint if endpoint is not None else os.getenv("EMBEDDING_URL", "")).rstrip("/")
        self.token_provider = token_provider or (lambda: service_token("EMBEDDING"))
        self.client = None
        self.transport = transport
        self.deadline = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None

    def _remaining(self):
        remaining = 120 if self.deadline is None else min(120, self.deadline - time.monotonic())
        if remaining <= 0:
            raise APIError(503, "QUALITY_TIMEOUT", "quality encoding budget exhausted")
        return remaining

    def _request(self, method, path, body=None):
        if not self.endpoint:
            raise APIError(503, "QWEN_UNAVAILABLE", "embedding service is not configured")
        self._remaining()
        try:
            token = self.token_provider()
            if self.client is None:
                ca = os.getenv("EMBEDDING_CA_FILE")
                verify = ssl.create_default_context(cafile=ca) if ca else True
                self.client = httpx.Client(
                    timeout=httpx.Timeout(120, connect=10), trust_env=False,
                    follow_redirects=False, verify=verify, transport=self.transport,
                )
            with self.client.stream(
                method, self.endpoint + path, json=body,
                headers={"Authorization": "Bearer " + token},
                timeout=httpx.Timeout(self._remaining(), connect=min(10, self._remaining())),
            ) as reply:
                if reply.status_code == 429:
                    raise APIError(429, "QUALITY_BUSY", "quality worker is busy; retry later")
                if reply.status_code == 422:
                    raise APIError(422, "QUALITY_INPUT_REJECTED", "quality worker rejected encoding input")
                if reply.status_code != 200:
                    raise APIError(503, "QWEN_UNAVAILABLE", "quality worker is unavailable")
                content = bytearray()
                for block in reply.iter_bytes():
                    self._remaining()
                    if len(content) + len(block) > MAX_RESPONSE_BYTES:
                        raise protocol_error()
                    content.extend(block)
        except APIError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            self._remaining()
            raise APIError(503, "QWEN_UNAVAILABLE", "quality worker request failed") from None
        self._remaining()
        try:
            result = json.loads(content)
        except (ValueError, UnicodeError):
            raise protocol_error() from None
        if not isinstance(result, dict):
            raise protocol_error()
        return result

    def descriptor(self):
        result = self._request("GET", "/v1/models")
        rows = result.get("data")
        if not isinstance(rows, list):
            raise protocol_error()
        quality = [row for row in rows if isinstance(row, dict) and row.get("id") == QUALITY_MODEL]
        if not quality:
            raise APIError(503, "QWEN_UNAVAILABLE", "quality worker is not configured")
        if len(quality) != 1:
            raise protocol_error()
        return QualityDescriptor.from_model(quality[0])

    def encode(self, texts, descriptor, purpose="document"):
        if (
            not isinstance(texts, list)
            or not 1 <= len(texts) <= descriptor.max_batch_size
            or any(not isinstance(text, str) or not text.strip() or len(text) > 32768 for text in texts)
            or purpose not in ("query", "document")
        ):
            raise APIError(422, "QUALITY_INPUT_REJECTED", "invalid quality encoding batch")
        result = self._request("POST", "/v1/embeddings", {
            "model": "quality", "input": texts, "purpose": purpose,
            "model_revision": descriptor.model_revision,
            "dimensions": descriptor.dimensions,
            "preprocessing_revision": descriptor.preprocessing_revision,
        })
        if (
            result.get("model") != descriptor.model_id
            or result.get("model_revision") != descriptor.model_revision
            or type(result.get("dimensions")) is not int
            or result["dimensions"] != descriptor.dimensions
            or result.get("preprocessing_revision") != descriptor.preprocessing_revision
        ):
            raise protocol_error()
        rows = result.get("data")
        if not isinstance(rows, list) or len(rows) != len(texts):
            raise protocol_error()
        vectors = [None] * len(texts)
        for row in rows:
            if not isinstance(row, dict):
                raise protocol_error()
            index = _integer(row.get("index"), 0, len(texts) - 1)
            vector = row.get("embedding")
            if vectors[index] is not None or not isinstance(vector, list) or len(vector) != descriptor.dimensions:
                raise protocol_error()
            if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in vector):
                raise protocol_error()
            try:
                vector = [float(value) for value in vector]
                norm = math.hypot(*vector)
            except (OverflowError, ValueError):
                raise protocol_error() from None
            if not math.isfinite(norm) or norm == 0 or any(not math.isfinite(value) for value in vector):
                raise protocol_error()
            vectors[index] = [value / norm for value in vector]
        return vectors
