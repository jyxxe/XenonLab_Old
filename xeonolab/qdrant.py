"""Private Qdrant REST adapter for derived XeLibrary vectors."""

import os
from pathlib import Path

import httpx

from .common import APIError

FAST_TEXT_COLLECTION = "fast_text"
FAST_TEXT_DIMENSIONS = 256


def configured():
    return any(os.getenv(name) for name in ("QDRANT_URL", "QDRANT_API_KEY_FILE", "QDRANT_CA_FILE"))


class QdrantAdapter:
    """Small REST client that never includes endpoint credentials in errors."""

    def __init__(self, *, url=None, api_key_file=None, ca_file=None, collection=FAST_TEXT_COLLECTION, transport=None):
        url = url or os.getenv("QDRANT_URL", "")
        api_key_file = api_key_file or os.getenv("QDRANT_API_KEY_FILE", "")
        ca_file = ca_file or os.getenv("QDRANT_CA_FILE")
        if not url or not api_key_file:
            raise APIError(503, "QDRANT_NOT_CONFIGURED", "Qdrant service configuration is incomplete")
        if not url.startswith("https://"):
            raise APIError(503, "QDRANT_TLS_REQUIRED", "Qdrant HTTPS endpoint is required")
        try:
            api_key = Path(api_key_file).read_text(encoding="utf-8").strip()
        except OSError:
            raise APIError(503, "QDRANT_NOT_CONFIGURED", "Qdrant service configuration is incomplete") from None
        if not api_key:
            raise APIError(503, "QDRANT_NOT_CONFIGURED", "Qdrant service configuration is incomplete")
        if ca_file and not Path(ca_file).is_file():
            raise APIError(503, "QDRANT_NOT_CONFIGURED", "Qdrant TLS trust configuration is incomplete")
        self.url = url.rstrip("/")
        self.collection = collection
        self.client = httpx.Client(
            timeout=15,
            trust_env=False,
            verify=ca_file or True,
            headers={"api-key": api_key},
            transport=transport,
        )

    def close(self):
        self.client.close()

    def request(self, method, path, *, json_body=None, allow_not_found=False):
        try:
            result = self.client.request(method, self.url + path, json=json_body)
            if allow_not_found and result.status_code == 404:
                return None
            if result.status_code >= 400:
                raise APIError(503, "QDRANT_UNAVAILABLE", "vector index request failed")
            payload = result.json()
        except APIError:
            raise
        except (httpx.HTTPError, ValueError):
            raise APIError(503, "QDRANT_UNAVAILABLE", "vector index request failed") from None
        if not isinstance(payload, dict) or payload.get("status") not in (None, "ok"):
            raise APIError(503, "QDRANT_UNAVAILABLE", "vector index request failed")
        return payload.get("result", {})

    def collection_path(self):
        return f"/collections/{self.collection}"

    def collection_info(self):
        return self.request("GET", self.collection_path(), allow_not_found=True)

    def create_collection(self):
        self.request(
            "PUT",
            self.collection_path(),
            json_body={
                "vectors": {FAST_TEXT_COLLECTION: {"size": FAST_TEXT_DIMENSIONS, "distance": "Cosine"}}
            },
        )

    def ensure_collection(self):
        result = self.collection_info()
        if result is None:
            self.create_collection()
            return
        vectors = result.get("config", {}).get("params", {}).get("vectors", {})
        vector = vectors.get(FAST_TEXT_COLLECTION) if isinstance(vectors, dict) else None
        if not isinstance(vector, dict) or vector.get("size") != FAST_TEXT_DIMENSIONS or vector.get("distance") != "Cosine":
            raise APIError(503, "QDRANT_SCHEMA_MISMATCH", "vector collection configuration is incompatible")

    def upsert(self, points):
        if points:
            self.request(
                "PUT",
                self.collection_path() + "/points?wait=true",
                json_body={"points": points},
            )

    def delete_ids(self, point_ids):
        if point_ids:
            self.request(
                "POST",
                self.collection_path() + "/points/delete?wait=true",
                json_body={"points": list(point_ids)},
            )

    def delete_filter(self, conditions):
        """Delete matching points; a missing collection is already clean."""
        result = self.request("GET", self.collection_path(), allow_not_found=True)
        if result is None:
            return
        self.request(
            "POST",
            self.collection_path() + "/points/delete?wait=true",
            json_body={"filter": {"must": conditions}},
        )

    def delete_row(self, table_id, row_id):
        self.delete_filter(
            [
                {"key": "table_id", "match": {"value": table_id}},
                {"key": "row_id", "match": {"value": row_id}},
            ]
        )

    def delete_table(self, table_id):
        self.delete_filter([{"key": "table_id", "match": {"value": table_id}}])

    def query(self, vector, limit, model_revision):
        result = self.request(
            "POST",
            self.collection_path() + "/points/query",
            json_body={
                "query": vector,
                "using": FAST_TEXT_COLLECTION,
                "limit": limit,
                "with_payload": True,
                "with_vector": False,
                "filter": {
                    "must": [
                        {"key": "model_revision", "match": {"value": model_revision}},
                        {"key": "chunker_version", "match": {"value": "chunker-v2"}},
                    ]
                },
            },
        )
        points = result.get("points", [])
        if not isinstance(points, list):
            raise APIError(503, "QDRANT_UNAVAILABLE", "vector index response is invalid")
        return points

    def delete_collection(self):
        self.request("DELETE", self.collection_path(), allow_not_found=True)
