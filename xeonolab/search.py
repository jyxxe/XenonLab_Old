"""Single-node exact-vector bootstrap index; facts always revalidated in .xl."""

import json
import logging
import math
import os
import threading
import time
from typing import Literal
from uuid import NAMESPACE_URL, uuid4, uuid5

import httpx
from fastapi import BackgroundTasks, Depends, Request
from pydantic import BaseModel

from .common import APIError, connect, response, scope
from .document_chunker import ChunkerBinding, DocumentChunkers
from .jobs import JobStore
from .qdrant import FAST_TEXT_COLLECTION, FAST_TEXT_DIMENSIONS, QdrantAdapter
from .qdrant import configured as qdrant_configured
from .quality_pipeline import QualityPipeline
from .query import path_matches
from .search_models import SEARCH_ERRORS, SearchResponse
from .services import Search


class IndexRequest(BaseModel):
    table_id: str


CHUNKER_VERSION = "chunker-v2"
COSINE_SCORE_TOLERANCE = 1e-6
CHUNK_TARGET_TOKENS = 108
CHUNK_OVERLAP_TOKENS = 16
CHUNK_HARD_LIMIT = 126
CHUNK_CONTEXT_LIMIT = 18


def chunk_tokens(tokenizer, text, prefix_ids):
    """Yield decoded token windows over text with the context prefix included.

    Windows overlap by CHUNK_OVERLAP_TOKENS; decode/re-encode may move token
    boundaries, so the hard limit is enforced on the re-encoded model input.
    """
    tokens = tokenizer.encode(text).ids
    if not tokens:
        return
    stride = CHUNK_TARGET_TOKENS - CHUNK_OVERLAP_TOKENS
    for offset in range(0, len(tokens), stride):
        window = prefix_ids + tokens[offset : offset + CHUNK_TARGET_TOKENS]
        encoded = tokenizer.encode(tokenizer.decode(window)).ids
        yield tokenizer.decode(encoded[:CHUNK_HARD_LIMIT])
        if offset + CHUNK_TARGET_TOKENS >= len(tokens):
            break


def chunk_documents(tokenizer, values, path, table_name):
    """Yield (column, chunk) for every non-empty string row value.

    Context follows plan.md §12: [PATH] [TABLE] [COLUMN] [TEXT]; [TITLE] stays
    unimplemented until a schema-level title convention exists.
    """
    context = tokenizer.encode(f"[PATH] {path} [TABLE] {table_name} ").ids
    for column, value in values.items():
        if not isinstance(value, str) or not value.strip():
            continue
        prefix = (context + tokenizer.encode(f"[COLUMN] {column} [TEXT] ").ids)[:CHUNK_CONTEXT_LIMIT]
        for chunk in chunk_tokens(tokenizer, value, prefix):
            yield column, chunk


def install(
    app, catalogue, file, authorize, allowed, request_acl, load_row, table_transaction, table_connect
):
    lock = threading.Lock()
    jobs = JobStore(catalogue)
    endpoint = os.getenv("EMBEDDING_URL")
    credential = os.getenv("EMBEDDING_SERVICE_TOKEN") or os.getenv("EMBEDDING_CLIENT_SECRET_FILE")
    tokenizer_path = os.getenv("D100M_TOKENIZER_PATH")
    logger = logging.getLogger(__name__)
    quality = QualityPipeline(app.state.metrics, str(catalogue))
    app.state.quality_pipeline = quality
    chunker_flag = os.getenv("XELIBRARY_CHUNKER_CAPABILITIES", "1")
    if chunker_flag not in {"0", "1"}:
        raise ValueError("XELIBRARY_CHUNKER_CAPABILITIES must be 0 or 1")
    app.state.document_chunkers = DocumentChunkers(
        ChunkerBinding(CHUNKER_VERSION, chunk_documents), enforce=chunker_flag == "1",
    )

    def configured():
        if not endpoint or not credential or not tokenizer_path:
            raise APIError(
                503, "SEARCH_NOT_CONFIGURED", "embedding URL, service token and D100M tokenizer required"
            )

    def backend_configured():
        configured_qdrant = qdrant_configured()
        if not configured_qdrant and getattr(app.state.settings, "mode", "production") != "development":
            raise APIError(503, "QDRANT_NOT_CONFIGURED", "Qdrant service configuration is required")
        return configured_qdrant

    def embedding(texts):
        with httpx.Client(timeout=120, trust_env=False) as client:
            from .oauth import service_token

            result = client.post(
                endpoint.rstrip("/") + "/v1/embeddings",
                headers={"Authorization": "Bearer " + service_token("EMBEDDING")},
                json={"model": "fast", "input": texts},
            )
        if result.status_code != 200:
            raise APIError(503, "EMBEDDING_UNAVAILABLE", "embedding worker request failed")
        result = result.json()
        return [r["embedding"] for r in result["data"]], result["model_revision"]

    def initialize(tid):
        with table_transaction(tid) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS __xl_vectors(row_id TEXT,revision INTEGER,
                       chunk_id TEXT PRIMARY KEY,column_name TEXT,body TEXT,vector TEXT,model_revision TEXT,
                       chunker_version TEXT)""")
            if "chunker_version" not in {row[1] for row in db.execute("PRAGMA table_info(__xl_vectors)")}:
                db.execute("ALTER TABLE __xl_vectors ADD COLUMN chunker_version TEXT")

    def remove_points(table_id, row_id=None):
        quality.cache.invalidate(table_id, row_id)
        if not backend_configured():
            return
        client = QdrantAdapter()
        try:
            if row_id is None:
                client.delete_table(table_id)
                operation = "table"
            else:
                client.delete_row(table_id, row_id)
                operation = "row"
            app.state.metrics.counter(
                "xelibrary_qdrant_cleanup_total",
                help="Successful remote Qdrant point cleanup operations.",
                resource=operation,
            )
        except APIError:
            app.state.metrics.counter(
                "xelibrary_qdrant_cleanup_failures_total",
                help="Failed remote Qdrant point cleanup operations.",
                resource="row" if row_id is not None else "table",
            )
            logger.warning("Qdrant point cleanup failed", extra={"table_id": table_id})
            raise
        finally:
            client.close()

    app.state.qdrant_remove_points = remove_points

    def point_id(table_id, row_id, revision, model_revision, column, ordinal, chunker_identity):
        identity = f"{table_id}:{row_id}:{revision}:{model_revision}:{chunker_identity}:{column}:{ordinal}"
        return str(uuid5(NAMESPACE_URL, identity))

    def run(job_id, tid, actor, chunker):
        if not lock.acquire(blocking=False):
            jobs.update(job_id, status="failed", error="index worker busy")
            return
        jobs.update(job_id, status="running")
        client = None
        try:
            from tokenizers import Tokenizer

            configured()
            chunkers = app.state.document_chunkers
            chunkers.check_active(chunker)
            table = authorize(actor, tid, "read")
            initialize(tid)
            use_qdrant = backend_configured()
            if use_qdrant:
                client = QdrantAdapter()
                client.ensure_collection()
            tokenizer = Tokenizer.from_file(tokenizer_path)
            with table_connect(tid) as db:
                ids = [r[0] for r in db.execute("SELECT _row_id FROM data WHERE _deleted_at IS NULL")]
            for rid in ids:
                table = authorize(actor, tid, "read")
                if not allowed(actor, table, "read", rid):
                    continue
                with table_connect(tid) as db:
                    row = load_row(db, rid)
                encoded, points = [], []
                for ordinal, (column, chunk) in enumerate(
                    chunkers.chunks(chunker, tokenizer, row["values"], table["path"], table["name"])
                ):
                    vectors, model_revision = embedding([chunk])
                    vector = vectors[0]
                    if use_qdrant and len(vector) != FAST_TEXT_DIMENSIONS:
                        raise APIError(
                            503, "EMBEDDING_DIMENSION_MISMATCH", "fast_text requires 256D vectors"
                        )
                    chunk_id = point_id(tid, rid, row["_revision"], model_revision, column, ordinal, chunker.binding.identity)
                    encoded.append(
                        (
                            rid,
                            row["_revision"],
                            chunk_id,
                            column,
                            chunk,
                            json.dumps(vector),
                            model_revision,
                            chunker.binding.identity,
                        )
                    )
                    points.append(
                        {
                            "id": chunk_id,
                            "vector": {FAST_TEXT_COLLECTION: vector},
                            "payload": {
                                "table_id": tid,
                                "row_id": rid,
                                "row_revision": row["_revision"],
                                "chunk_id": chunk_id,
                                "column": column,
                                "path": table["path"],
                                "model_revision": model_revision,
                                "chunker_version": chunker.binding.identity,
                            },
                        }
                    )

                # Serialize the source revision check and remote upsert against
                # row writes. Qdrant IDs are deterministic, so retries overwrite
                # points from an interrupted run instead of duplicating them.
                with table_transaction(tid) as db:
                    chunkers.check_active(chunker)
                    current = load_row(db, rid)
                    current_table = authorize(actor, tid, "read")
                    if current["_revision"] != row["_revision"] or not allowed(
                        actor, current_table, "read", rid
                    ):
                        continue
                    if current_table["path"] != table["path"] or current_table["name"] != table["name"]:
                        continue
                    old_ids = [r[0] for r in db.execute("SELECT chunk_id FROM __xl_vectors WHERE row_id=?", (rid,))]
                    if client:
                        client.upsert(points)
                        keep_ids = {point["id"] for point in points}
                        client.delete_ids(point_id for point_id in old_ids if point_id not in keep_ids)
                    db.execute("DELETE FROM __xl_vectors WHERE row_id=?", (rid,))
                    db.executemany("INSERT INTO __xl_vectors VALUES(?,?,?,?,?,?,?,?)", encoded)
                    db.execute(
                        "UPDATE __xl_changes SET indexed_revision=? WHERE row_id=? AND revision<=?",
                        (row["_revision"], rid, row["_revision"]),
                    )
            jobs.update(job_id, status="completed")
        except Exception:
            app.state.metrics.counter("xelibrary_index_failures_total", help="Failed XeLibrary index jobs.")
            jobs.update(job_id, status="failed", error="indexing failed; check search dependencies")
        finally:
            if client:
                client.close()
            lock.release()

    @app.post("/api/v1/index/reindex", status_code=202, responses={
        404: {"description": "Table or document chunker capability is unavailable."},
        409: {"description": "Document chunker capability version is incompatible."},
        503: {"description": "Index dependencies or the chunker capability configuration are unavailable."},
    })
    def reindex(
        body: IndexRequest,
        request: Request,
        background: BackgroundTasks,
        actor=Depends(scope("xelibrary:admin")),
    ):
        configured()
        authorize(actor, body.table_id, "read")
        chunker = app.state.document_chunkers.resolve(actor)
        if backend_configured():
            client = QdrantAdapter()
            try:
                client.ensure_collection()
            finally:
                client.close()
        job_id = str(uuid4())
        with table_transaction(body.table_id):
            jobs.create(job_id, actor.sub, body.table_id)
        background.add_task(run, job_id, body.table_id, actor, chunker)
        return response(request, {"job_id": job_id})

    @app.get("/api/v1/jobs")
    def job_list(
        request: Request,
        table_id: str = "",
        status: Literal["queued", "running", "completed", "failed"] | None = None,
        limit: int = 50,
        actor=Depends(scope("xelibrary:admin")),
    ):
        """Embedding/index queue view for administrators; reindex itself stays admin-only."""
        if not 1 <= limit <= 200:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        return response(
            request,
            jobs.list(table_id=table_id or None, status=status, owner=actor.sub, limit=limit),
            counts=jobs.counts(table_id=table_id or None, owner=actor.sub),
        )

    @app.get("/api/v1/embedding/jobs")
    def embedding_job_list(
        request: Request,
        table_id: str = "",
        status: Literal["queued", "running", "completed", "failed"] | None = None,
        limit: int = 50,
        actor=Depends(scope("embedding:read")),
    ):
        """Return only the authenticated caller's XeLibrary indexing jobs."""
        if not 1 <= limit <= 200:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        owned_jobs = jobs.list(table_id=table_id or None, status=status, owner=actor.sub, limit=limit)
        fields = ("job_id", "table_id", "status", "created_at", "updated_at", "error")
        rows = [{key: job.get(key) for key in fields} for job in owned_jobs]
        return response(
            request,
            rows,
            counts=jobs.counts(table_id=table_id or None, owner=actor.sub),
        )

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str, request: Request, actor=Depends(scope("xelibrary:admin"))):
        data = jobs.get(job_id)
        if data is None:
            raise APIError(404, "JOB_NOT_FOUND", "job not found")
        if data["owner"] != actor.sub:
            raise APIError(403, "FORBIDDEN", "job belongs to another caller")
        return response(request, data)

    @app.get("/api/v1/tables/{tid}/index-status")
    def index_status(tid: str, request: Request, actor=Depends(scope("xelibrary:read"))):
        """Per-table index visibility for every reader of the table (issue #72).

        Derives live coverage from the table itself instead of the admin-only
        job log: rows at their current revision are counted through
        __xl_vectors, rows still waiting through __xl_changes.indexed_revision.
        Model/chunker staleness is deliberately not folded into these numbers;
        search responses already surface it explicitly.
        """
        authorize(actor, tid, "read")
        with table_connect(tid) as db:
            names = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            row_count = db.execute("SELECT COUNT(*) FROM data WHERE _deleted_at IS NULL").fetchone()[0]
            embedded_rows = chunk_count = pending_rows = 0
            if "__xl_vectors" in names:
                embedded_rows = db.execute(
                    "SELECT COUNT(DISTINCT v.row_id) FROM __xl_vectors v "
                    "JOIN data d ON d._row_id=v.row_id AND d._deleted_at IS NULL "
                    "WHERE v.revision=d._revision"
                ).fetchone()[0]
                chunk_count = db.execute("SELECT COUNT(*) FROM __xl_vectors").fetchone()[0]
            if "__xl_changes" in names:
                pending_rows = db.execute(
                    "SELECT COUNT(DISTINCT row_id) FROM __xl_changes WHERE revision > indexed_revision"
                ).fetchone()[0]
        latest = jobs.list(table_id=tid, limit=1)
        raw_last = latest[0] if latest else None
        job_status = (raw_last or {}).get("status")
        if raw_last is None:
            last_job = None
        elif raw_last.get("owner") == actor.sub:
            # The caller's own job carries full metadata, including errors.
            last_job = raw_last
        else:
            # Shared-table readers only get a minimal summary: no job id, owner
            # subject, error payload or other caller-identifying metadata.
            last_job = {key: raw_last[key] for key in ("status", "created_at") if key in raw_last}
        if job_status == "queued":
            state = "queued"
        elif job_status == "running":
            state = "indexing"
        elif job_status == "failed":
            state = "failed"
        elif row_count == 0:
            state = "empty"
        elif embedded_rows == row_count:
            state = "ready"
        elif pending_rows > 0 and embedded_rows == 0:
            # Changes exist but no row has ever been embedded.
            state = "not_indexed"
        elif pending_rows > 0 or 0 < embedded_rows < row_count:
            state = "stale"
        else:
            state = "not_indexed"
        return response(
            request,
            {
                "table_id": tid,
                "state": state,
                "row_count": row_count,
                "embedded_rows": embedded_rows,
                "missing_rows": max(row_count - embedded_rows, 0),
                "pending_rows": pending_rows,
                "chunk_count": chunk_count,
                "last_job": last_job,
            },
        )

    def exact_bootstrap(body, actor, query, model_revision, candidate_limit):
        """Use the explicitly development-only SQLite bootstrap backend."""
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables")]
        results, missing = [], 0
        for table in tables:
            if not path_matches(table["path"], body.path):
                continue
            permits = request_acl(actor, table)
            if not permits("read"):
                continue
            initialize(table["id"])
            with table_connect(table["id"]) as db:
                for raw in db.execute("SELECT _row_id FROM data WHERE _deleted_at IS NULL"):
                    rid = raw[0]
                    if not permits("read", rid):
                        continue
                    row = load_row(db, rid)
                    if any(row["values"].get(key) != value for key, value in body.filters.items()):
                        continue
                    hits = db.execute(
                        "SELECT * FROM __xl_vectors WHERE row_id=? AND revision=? AND model_revision=?"
                        " AND chunker_version=?",
                        (rid, row["_revision"], model_revision, app.state.document_chunkers.identity),
                    ).fetchall()
                    compatible = [hit for hit in hits if len(json.loads(hit["vector"])) == len(query)
                                  and hit["column_name"] in row["values"]]
                    if not compatible:
                        missing += 1
                        continue
                    best = max(
                        compatible,
                        key=lambda hit: sum(x * y for x, y in zip(query, json.loads(hit["vector"]))),
                    )
                    current = load_row(db, rid)
                    if current["_revision"] != row["_revision"] or not permits("read", rid):
                        continue
                    results.append(
                        {
                            "table_id": table["id"],
                            "row_id": rid,
                            "row_revision": current["_revision"],
                            "path": table["path"],
                            "score": sum(x * y for x, y in zip(query, json.loads(best["vector"]))),
                            "score_type": "d100m_cosine",
                            "snippet": str(current["values"][best["column_name"]])[:240],
                            "matched_chunks": [{"chunk_id": best["chunk_id"], "column": best["column_name"]}],
                            "approximate": False,
                            "indexed_revision": best["revision"],
                            "_context": (
                                table["name"],
                                db.execute("SELECT schema_revision FROM __xl_meta").fetchone()[0],
                            ),
                            "_chunks": [dict(best)],
                        }
                    )
        results.sort(key=lambda result: result["score"], reverse=True)
        return results[:candidate_limit], {
            "partial": bool(missing or len(results) > candidate_limit),
            "missing_embeddings": missing,
            "index_backend": "sqlite_exact_bootstrap",
            "model_revision": model_revision,
            "candidate_limit": candidate_limit,
            "candidate_count": min(len(results), candidate_limit),
        }

    def fresh_candidate(hit, body, actor):
        """Fresh ACL/source checks after slow dependencies, including cache hits."""
        try:
            table = authorize(actor, hit["table_id"], "read")
            if not allowed(actor, table, "read", hit["row_id"]):
                return False
            if table["path"] != hit["path"] or not path_matches(table["path"], body.path):
                return False
            with table_connect(hit["table_id"]) as db:
                row = load_row(db, hit["row_id"])
                if row["_revision"] != hit["row_revision"]:
                    return False
                if any(row["values"].get(key) != value for key, value in body.filters.items()):
                    return False
                context = (table["name"], db.execute("SELECT schema_revision FROM __xl_meta").fetchone()[0])
                if "_context" in hit and context != hit["_context"]:
                    return False
                chunks = hit.get("_chunks", [])
                if chunks:
                    indexed_rows = db.execute(
                        "SELECT chunk_id,column_name,model_revision,chunker_version,body FROM __xl_vectors "
                        "WHERE row_id=? AND revision=?",
                        (hit["row_id"], hit["row_revision"]),
                    ).fetchall()
                    indexed = {
                        (
                            entry["chunk_id"],
                            entry["column_name"],
                            entry["model_revision"],
                            entry["chunker_version"],
                        ): entry["body"]
                        for entry in indexed_rows
                    }
                    for chunk in chunks:
                        key = (
                            chunk["chunk_id"],
                            chunk["column_name"],
                            chunk["model_revision"],
                            app.state.document_chunkers.identity,
                        )
                        if indexed.get(key) != chunk["body"]:
                            return False
                    column = hit["matched_chunks"][0]["column"]
                    hit["snippet"] = str(row["values"][column])[:240]
                else:
                    hit["snippet"] = " ".join(str(value) for value in row["values"].values())[:240]
            # Re-read table/row policy rather than reusing a pre-worker request_acl closure.
            final_table = authorize(actor, hit["table_id"], "read")
            return (
                final_table["path"] == table["path"]
                and final_table["name"] == table["name"]
                and allowed(actor, final_table, "read", hit["row_id"])
            )
        except APIError as error:
            if error.status in (403, 404):
                return False
            raise

    app.state.search_revalidate = fresh_candidate

    def collect_fast(body, actor):
        configured()
        use_qdrant = backend_configured()
        vectors, model_revision = embedding([body.query])
        query = vectors[0]
        candidate_limit = (
            min(max(body.limit * 5, 100), 500)
            if body.mode == "quality"
            else min(max(body.limit * 10, 100), 1000)
        )
        if not use_qdrant:
            return exact_bootstrap(body, actor, query, model_revision, candidate_limit)
        if len(query) != FAST_TEXT_DIMENSIONS:
            raise APIError(503, "EMBEDDING_DIMENSION_MISMATCH", "fast_text requires 256D vectors")
        client = QdrantAdapter()
        try:
            client.ensure_collection()
            candidates = client.query(query, candidate_limit, model_revision)
        finally:
            client.close()

        # Qdrant payload is a candidate hint only. Re-read and reauthorize every
        # candidate against the current catalogue, ACL and .xl revision.
        by_row, stale_rows = {}, set()
        for candidate in candidates:
            payload = candidate.get("payload") if isinstance(candidate, dict) else None
            raw_score = candidate.get("score") if isinstance(candidate, dict) else None
            if (
                not isinstance(payload, dict)
                or isinstance(raw_score, bool)
                or not isinstance(raw_score, (int, float))
            ):
                continue
            try:
                score = float(raw_score)
            except (OverflowError, ValueError):
                continue
            if (
                not math.isfinite(score)
                or score < -1.0 - COSINE_SCORE_TOLERANCE
                or score > 1.0 + COSINE_SCORE_TOLERANCE
            ):
                continue
            tid, rid = payload.get("table_id"), payload.get("row_id")
            row_revision = payload.get("row_revision")
            chunk_id, column = payload.get("chunk_id"), payload.get("column")
            if (
                not isinstance(tid, str)
                or not isinstance(rid, str)
                or not isinstance(row_revision, int)
                or not isinstance(chunk_id, str)
                or not isinstance(column, str)
                or candidate.get("id") != chunk_id
                or payload.get("model_revision") != model_revision
                or payload.get("chunker_version") != app.state.document_chunkers.identity
            ):
                continue
            try:
                table = authorize(actor, tid, "read")
                permits = request_acl(actor, table)
                if not permits("read", rid) or not path_matches(table["path"], body.path):
                    continue
                if payload.get("path") != table["path"]:
                    stale_rows.add((tid, rid))
                    continue
                with table_connect(tid) as db:
                    row = load_row(db, rid)
                    if row["_revision"] != row_revision:
                        stale_rows.add((tid, rid))
                        continue
                    indexed = db.execute(
                        "SELECT * FROM __xl_vectors WHERE row_id=? AND revision=? AND chunk_id=? "
                        "AND column_name=? AND model_revision=? AND chunker_version=?",
                        (rid, row_revision, chunk_id, column, model_revision, app.state.document_chunkers.identity),
                    ).fetchone()
                    if not indexed or column not in row["values"]:
                        stale_rows.add((tid, rid))
                        continue
                    if any(row["values"].get(key) != value for key, value in body.filters.items()):
                        continue
                    context = (
                        table["name"],
                        db.execute("SELECT schema_revision FROM __xl_meta").fetchone()[0],
                    )
                    current = load_row(db, rid)
                    if current["_revision"] != row_revision or not permits("read", rid):
                        continue
                    if authorize(actor, tid, "read")["path"] != table["path"]:
                        stale_rows.add((tid, rid))
                        continue
            except APIError as error:
                if error.status in (403, 404):
                    continue
                raise
            key = (tid, rid)
            result = {
                "table_id": tid,
                "row_id": rid,
                "row_revision": row_revision,
                "path": table["path"],
                # Accept only tiny serialization/rounding excursions, and keep
                # returned cosine scores inside their documented interval.
                "score": min(1.0, max(-1.0, float(score))),
                "score_type": "d100m_cosine",
                "snippet": str(current["values"][column])[:240],
                "matched_chunks": [{"chunk_id": chunk_id, "column": column}],
                "approximate": True,
                "indexed_revision": row_revision,
                "_context": context,
                "_chunks": [dict(indexed)],
            }
            if key not in by_row:
                by_row[key] = result
            else:
                prior = by_row[key]
                if chunk_id not in {chunk["chunk_id"] for chunk in prior["_chunks"]}:
                    prior["_chunks"].append(dict(indexed))
                if result["score"] > prior["score"]:
                    prior.update({name: value for name, value in result.items() if name != "_chunks"})
        results = sorted(
            by_row.values(), key=lambda result: (-result["score"], result["table_id"], result["row_id"])
        )
        missing = len(stale_rows)
        return results, {
            "partial": bool(missing or (len(candidates) >= candidate_limit and len(results) < body.limit)),
            "missing_embeddings": missing,
            "index_backend": "qdrant_fast_text",
            "model_revision": model_revision,
            "candidate_limit": candidate_limit,
            "candidate_count": len(candidates),
        }

    def collect_semantic(body, actor, request_id="", deadline=None):
        started = time.monotonic()
        if body.mode == "quality" and deadline is None:
            deadline = quality.clock() + quality.time_budget
        try:
            try:
                hits, meta = collect_fast(body, actor)
            finally:
                if body.mode == "quality":
                    app.state.metrics.observe(
                        "xelibrary_quality_stage_seconds",
                        time.monotonic() - started,
                        help="Quality pipeline stage duration.",
                        stage="shortlist",
                    )
            meta["actual_mode"] = "quality" if body.mode == "quality" else "fast"
            if body.mode == "quality":
                app.state.metrics.counter(
                    "xelibrary_quality_candidates_total",
                    len(hits),
                    help="Candidate chunks handled by quality pipeline.",
                    outcome="authorized_rows",
                )
                before = len(hits)
                hits, quality_meta = quality.rerank(
                    body.query, hits, actor, lambda hit: fresh_candidate(hit, body, actor), deadline=deadline
                )
                # A final independent gate also protects the interval after reranking.
                hits = [hit for hit in hits if fresh_candidate(hit, body, actor)]
                quality.check_deadline(deadline)
                discarded = before - len(hits)
                app.state.metrics.counter(
                    "xelibrary_quality_candidates_total",
                    discarded,
                    help="Candidate chunks handled by quality pipeline.",
                    outcome="discarded_rows",
                )
                meta.update(quality_meta)
                meta["partial"] = bool(
                    meta["partial"] or discarded or meta["candidate_count"] >= meta["candidate_limit"]
                )
                app.state.metrics.counter(
                    "xelibrary_quality_requests_total", help="Quality request outcomes.", result="ok"
                )
            return hits, meta
        except APIError as error:
            if body.mode == "quality":
                app.state.metrics.counter(
                    "xelibrary_quality_requests_total", help="Quality request outcomes.", result="error"
                )
                app.state.metrics.counter(
                    "xelibrary_quality_errors_total", help="Quality request errors.", code=error.code
                )
                logger.warning("Quality search failed", extra={"request_id": request_id, "code": error.code})
            raise
        except Exception:
            if body.mode == "quality":
                app.state.metrics.counter("xelibrary_quality_requests_total",
                                          help="Quality request outcomes.", result="error")
                app.state.metrics.counter("xelibrary_quality_errors_total",
                                          help="Quality request errors.", code="INTERNAL_ERROR")
                logger.warning("Quality search failed", extra={"request_id": request_id,
                                                              "code": "INTERNAL_ERROR"})
            raise

    app.state.semantic_collect = collect_semantic

    @app.post(
        "/api/v1/search/semantic",
        response_model=SearchResponse,
        response_model_exclude_none=True,
        responses=SEARCH_ERRORS,
    )
    def semantic(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
        hits, meta = collect_semantic(body, actor, request.state.request_id)
        return response(request, hits[: body.limit], **meta)
