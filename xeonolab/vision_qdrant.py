"""Descriptor-specific, permanent Qwen vision collections on production Qdrant."""

from .common import APIError
from .qdrant import QdrantAdapter


class VisionAdapter(QdrantAdapter):
    def __init__(self, space, **kwargs):
        self.space = space
        super().__init__(collection=space.collection(), **kwargs)

    def create_collection(self):
        self.request("PUT", self.collection_path(), json_body={
            "vectors": {"vision": {"size": self.space.dimensions, "distance": "Cosine"}},
        })

    def ensure_collection(self):
        result = self.collection_info()
        if result is None:
            self.create_collection()
            return
        if not isinstance(result, dict):
            raise APIError(503, "QDRANT_SCHEMA_MISMATCH", "vision collection configuration is incompatible")
        vector = result.get("config", {}).get("params", {}).get("vectors", {}).get("vision")
        if not isinstance(vector, dict) or vector.get("size") != self.space.dimensions or vector.get("distance") != "Cosine":
            raise APIError(503, "QDRANT_SCHEMA_MISMATCH", "vision collection configuration is incompatible")

    def query(self, vector, limit):
        if self.collection_info() is None:
            return []
        result = self.request("POST", self.collection_path() + "/points/query", json_body={
            "query": vector, "using": "vision", "limit": limit, "with_payload": True, "with_vector": False,
            "filter": {"must": [{"key": "space_key", "match": {"value": self.space.key()}}]},
        })
        points = result.get("points")
        if not isinstance(points, list):
            raise APIError(503, "QDRANT_UNAVAILABLE", "vision candidates are invalid")
        return points
