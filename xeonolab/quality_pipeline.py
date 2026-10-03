"""Second-stage embeddings over authorized current candidates, not full-library encoding."""

import hashlib
import threading
import time

from .common import APIError
from .quality_cache import QualityCache


def row_key(hit):
    return hit["table_id"], hit["row_id"]


def ranked_fusion(lists, score_type, weights=None):
    """Fuse ranks, never incompatible cosine/BM25 score magnitudes."""
    weights = weights or [1] * len(lists)
    by_row = {}
    for weight, hits in zip(weights, lists):
        seen = set()
        for rank, hit in enumerate(hits, 1):
            key = row_key(hit)
            if key in seen:
                continue
            seen.add(key)
            if key not in by_row:
                by_row[key] = {**hit, "score": 0.0, "score_type": score_type}
            by_row[key]["score"] += weight / (60 + rank)
    return sorted(by_row.values(), key=lambda hit: (-hit["score"], row_key(hit)))


class QualityPipeline:
    def __init__(
        self, metrics, namespace, client_factory=None, cache=None, time_budget=120, clock=time.monotonic
    ):
        self.metrics = metrics
        self.namespace = namespace
        self.client_factory = client_factory
        self.cache = cache or QualityCache()
        if not 0 < time_budget <= 120:
            raise ValueError("quality request budget must be within 120 seconds")
        self.time_budget = time_budget
        self.clock = clock
        self.lock = threading.Lock()
        metrics.gauge_provider(
            "xelibrary_quality_in_flight",
            "Active quality reranking requests.",
            lambda: [(None, int(self.lock.locked()))],
        )
        metrics.gauge_provider(
            "xelibrary_quality_cache_entries",
            "Cached candidate quality vectors.",
            lambda: [(None, self.cache.count())],
        )

    def _time(self, stage, started):
        self.metrics.observe(
            "xelibrary_quality_stage_seconds",
            time.monotonic() - started,
            help="Quality pipeline stage duration.",
            stage=stage,
        )

    def check_deadline(self, deadline):
        if self.clock() >= deadline:
            raise APIError(503, "QUALITY_TIMEOUT", "quality request budget exhausted")

    def rerank(self, query, hits, actor, validate, deadline=None):
        deadline = deadline if deadline is not None else self.clock() + self.time_budget
        self.check_deadline(deadline)
        if not self.lock.acquire(blocking=False):
            raise APIError(429, "QUALITY_BUSY", "quality pipeline busy; retry later", {"Retry-After": "1"})
        try:
            return self._rerank(query, hits, actor, validate, deadline)
        finally:
            self.lock.release()

    def _rerank(self, query, hits, actor, validate, deadline):
        factory = self.client_factory
        if factory is None:
            from .quality_provider import QualityClient

            factory = QualityClient
        with factory() as client:
            client.deadline = deadline
            self.check_deadline(deadline)
            descriptor = client.descriptor()
            self.check_deadline(deadline)
            started = time.monotonic()
            try:
                query_vector = client.encode([query], descriptor, purpose="query")[0]
                self.check_deadline(deadline)
            finally:
                self._time("query", started)
            principal = (actor.sub, tuple(actor.ancestors), tuple(sorted(actor.scopes)))
            vectors, pending = {}, []
            cache_hits = 0
            authorized = [hit for hit in hits if validate(hit)]
            for hit in authorized:
                for chunk in hit["_chunks"]:
                    text_hash = hashlib.sha256(chunk["body"].encode()).hexdigest()
                    key = (
                        self.namespace,
                        principal,
                        descriptor.space_key,
                        *row_key(hit),
                        hit["row_revision"],
                        hit["path"],
                        hit["_context"],
                        chunk["chunk_id"],
                        chunk["model_revision"],
                        chunk["chunker_version"],
                        text_hash,
                    )
                    vector = self.cache.get(key)
                    if vector is not None:
                        vectors[key] = vector
                        cache_hits += 1
                    else:
                        pending.append((key, hit, chunk))
            self.metrics.counter(
                "xelibrary_quality_cache_total",
                cache_hits,
                help="Quality candidate cache lookups.",
                result="hit",
            )
            self.metrics.counter(
                "xelibrary_quality_cache_total",
                len(pending),
                help="Quality candidate cache lookups.",
                result="miss",
            )
            started = time.monotonic()
            encoded_count = 0
            self.metrics.counter(
                "xelibrary_quality_candidates_total",
                0,
                help="Candidate chunks handled by quality pipeline.",
                outcome="encoded",
            )
            try:
                for offset in range(0, len(pending), descriptor.max_batch_size):
                    self.check_deadline(deadline)
                    batch = pending[offset : offset + descriptor.max_batch_size]
                    # Earlier batches can wait long enough for permission/source
                    # changes. Do not send another batch using that old decision.
                    batch_hits = {row_key(hit): hit for _, hit, _ in batch}
                    permitted = {key for key, hit in batch_hits.items() if validate(hit)}
                    batch = [item for item in batch if row_key(item[1]) in permitted]
                    if not batch:
                        continue
                    encoded = client.encode(
                        [chunk["body"] for _, _, chunk in batch], descriptor, purpose="document"
                    )
                    self.check_deadline(deadline)
                    encoded_count += len(batch)
                    self.metrics.counter(
                        "xelibrary_quality_candidates_total",
                        len(batch),
                        help="Candidate chunks handled by quality pipeline.",
                        outcome="encoded",
                    )
                    valid = {key: validate(hit) for key, hit in batch_hits.items()}
                    for (key, hit, _), vector in zip(batch, encoded):
                        if valid[row_key(hit)]:
                            vectors[key] = vector
                            evicted = self.cache.put(key, vector)
                            self.metrics.counter(
                                "xelibrary_quality_cache_evictions_total",
                                evicted,
                                help="Evicted candidate vectors.",
                            )
            finally:
                self._time("candidate", started)
            started = time.monotonic()
            quality = []
            for hit in authorized:
                if not validate(hit):
                    continue
                scores = [
                    (sum(x * y for x, y in zip(query_vector, vector)), key[8])
                    for key, vector in vectors.items()
                    if key[3:5] == row_key(hit)
                ]
                if scores:
                    score, chunk_id = sorted(scores, key=lambda item: (-item[0], item[1]))[0]
                    chunk = next(chunk for chunk in hit["_chunks"] if chunk["chunk_id"] == chunk_id)
                    quality.append(
                        {
                            **hit,
                            "score": score,
                            "matched_chunks": [
                                {"chunk_id": chunk_id, "column": chunk["column_name"]},
                            ],
                        }
                    )
            quality.sort(key=lambda hit: (-hit["score"], row_key(hit)))
            # Give the second stage priority without adding unlike model scores.
            fast = [hit for hit in hits if row_key(hit) in {row_key(row) for row in quality}]
            fused = ranked_fusion([quality, fast], "quality_rrf", [2, 1])
            self.check_deadline(deadline)
            self._time("rerank", started)
            return fused, {
                "quality_model_id": descriptor.model_id,
                "quality_model_revision": descriptor.model_revision,
                "quality_dimensions": descriptor.dimensions,
                "quality_preprocessing_revision": descriptor.preprocessing_revision,
                "cache_hits": cache_hits,
                "encoded_chunks": encoded_count,
                "quality_fusion": "rrf_k60_quality2_fast1",
            }
