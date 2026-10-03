"""Image/text-to-image search and rebuildable vision attachment indexes.

Only the embedding facade encodes images. Qdrant payloads are candidate hints;
current .xl bindings, hashes, revisions and ACLs decide visibility.
"""

import json
import logging
import math
import threading
import time
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from fastapi import BackgroundTasks, Depends, File, Form, Request, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .common import APIError, connect, response, scope
from .jobs import JobStore
from .query import path_matches
from .services import Search
from .vision_provider import IMAGE_MAX_BYTES, VisionProvider, VisionSpace, validate_image
from .vision_qdrant import VisionAdapter


class VisionIndexRequest(BaseModel):
    table_id: str = Field(min_length=1, max_length=64)


class VisionJob(BaseModel):
    job_id: str
    space: VisionSpace


class RequestMeta(BaseModel):
    request_id: str


class VisionJobResponse(BaseModel):
    data: VisionJob
    meta: RequestMeta


class MatchedAttachment(BaseModel):
    attachment_id: str
    object_hash: str


class VisionHit(BaseModel):
    table_id: str
    row_id: str
    row_revision: int
    path: str
    score: float
    score_type: Literal["qwen3vl_cosine"] = "qwen3vl_cosine"
    snippet: str
    matched_chunks: list[dict] = Field(default_factory=list)
    matched_attachments: list[MatchedAttachment]
    approximate: bool = True
    indexed_revision: int


class VisionMeta(RequestMeta):
    partial: bool
    missing_embeddings: int
    index_backend: Literal["qdrant_vision"] = "qdrant_vision"
    space: VisionSpace
    candidate_limit: int
    coverage_truncated: bool
    coverage_snapshot: Literal[True] = True


class VisionErrorDetail(BaseModel):
    code: str
    message: str
    details: dict
    request_id: str


class VisionErrorResponse(BaseModel):
    error: VisionErrorDetail


VISION_ERRORS = {
    status: {"model": VisionErrorResponse, "description": description}
    for status, description in {
        401: "Authentication required", 403: "Current scope or ACL denied", 404: "Source attachment not found",
        409: "Source attachment changed", 413: "Image exceeds byte limit", 422: "Invalid input or model space",
        429: "Embedding worker busy", 503: "Model, facade, storage or vector index unavailable",
    }.items()
}


class VisionSearchResponse(BaseModel):
    data: list[VisionHit]
    meta: VisionMeta


def references_exist(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE name='__xl_vision_refs'").fetchone() is not None


def initialize(db, space):
    db.execute("""CREATE TABLE IF NOT EXISTS __xl_vision_refs(
        point_id TEXT PRIMARY KEY,attachment_id TEXT NOT NULL,row_id TEXT NOT NULL,
        row_revision INTEGER NOT NULL,object_hash TEXT NOT NULL,path TEXT NOT NULL,
        collection TEXT NOT NULL,space TEXT NOT NULL,indexed_at REAL NOT NULL)
    """)
    db.execute("CREATE INDEX IF NOT EXISTS vision_attachment ON __xl_vision_refs(attachment_id)")
    # Persist known spaces before any remote upsert. A process interruption
    # before committing a point reference must not make remote cleanup blind.
    db.execute("CREATE TABLE IF NOT EXISTS __xl_vision_spaces(collection TEXT PRIMARY KEY,space TEXT NOT NULL)")
    db.execute("INSERT OR IGNORE INTO __xl_vision_spaces VALUES(?,?)", (space.collection(), space.model_dump_json()))


def remove_references(db, table_id, row_id=None, attachment_id=None):
    """Called under the existing table write lock and source transaction."""
    if not references_exist(db):
        return
    conditions, args = [], []
    if row_id is not None:
        conditions.append("row_id=?")
        args.append(row_id)
    if attachment_id is not None:
        conditions.append("attachment_id=?")
        args.append(attachment_id)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    refs = db.execute("SELECT collection,space FROM __xl_vision_refs" + where, args).fetchall()
    if db.execute("SELECT 1 FROM sqlite_master WHERE name='__xl_vision_spaces'").fetchone():
        refs += db.execute("SELECT collection,space FROM __xl_vision_spaces").fetchall()
    spaces = {}
    for ref in refs:
        space = VisionSpace.model_validate_json(ref["space"])
        if space.collection() != ref["collection"]:
            raise APIError(503, "VISION_INDEX_INCOMPATIBLE", "vision reference space is incompatible")
        spaces[space.collection()] = space
    remote_filter = [{"key": "table_id", "match": {"value": table_id}}]
    if row_id is not None:
        remote_filter.append({"key": "row_id", "match": {"value": row_id}})
    if attachment_id is not None:
        remote_filter.append({"key": "attachment_id", "match": {"value": attachment_id}})
    for space in spaces.values():
        client = VisionAdapter(space)
        try:
            # Current provider availability is irrelevant to old-space cleanup.
            client.delete_filter(remote_filter)
        finally:
            client.close()
    db.execute("DELETE FROM __xl_vision_refs" + where, args)


def install(app, catalogue, authorize, allowed, load_row, table_transaction, table_connect, resolve_attachment, request_acl, attachment_is_image):
    jobs = JobStore(catalogue)
    index_lock = threading.Lock()
    app.state.vision_remove_points = remove_references
    metrics = app.state.metrics
    logger = logging.getLogger(__name__)

    def record(operation, result, code=None):
        metrics.counter("xelibrary_vision_requests_total", help="Vision requests by operation and result.",
                        operation=operation, result=result)
        if code:
            metrics.counter("xelibrary_vision_errors_total", help="Vision errors by operation and code.",
                            operation=operation, code=code)

    def run(job_id, tid, actor, space):
        if not index_lock.acquire(blocking=False):
            jobs.update(job_id, status="failed", error="vision index worker busy", error_code="EMBEDDING_BUSY")
            return
        provider, client = None, None
        indexed, skipped = 0, 0
        jobs.update(job_id, status="running", indexed=0, skipped=0)
        try:
            provider = VisionProvider()
            if provider.space() != space:
                raise APIError(503, "MODEL_SPACE_MISMATCH", "vision model space changed before indexing")
            client = VisionAdapter(space)
            client.ensure_collection()
            with table_transaction(tid) as db:
                initialize(db, space)
            cursor = ""
            while True:
                authorize(actor, tid, "read")
                with table_connect(tid) as db:
                    batch = [dict(ref) for ref in db.execute(
                        "SELECT attachment_id,row_id FROM __xl_attachments WHERE attachment_id>? "
                        "ORDER BY attachment_id LIMIT 64", (cursor,),
                    )]
                if not batch:
                    break
                for item in batch:
                    cursor = item["attachment_id"]
                    try:
                        metadata = resolve_attachment(actor, tid, cursor, include_payload=False)
                        if not attachment_is_image(metadata):
                            skipped += 1
                            continue
                        source = resolve_attachment(actor, tid, cursor)
                        validate_image(source["payload"])
                    except APIError as error:
                        if error.code == "INVALID_IMAGE" or error.status in {403, 404}:
                            skipped += 1
                            continue
                        raise
                    vector = provider.encode(space, payload=source["payload"], purpose="document")
                    point_id = str(uuid5(NAMESPACE_URL, json.dumps([
                        tid, source["row_id"], source["row_revision"], cursor,
                        source["object_hash"], space.model_dump(),
                    ], sort_keys=True)))
                    with table_transaction(tid) as db:
                        current = load_row(db, source["row_id"])
                        table = authorize(actor, tid, "read", source["row_id"])
                        binding = db.execute("SELECT * FROM __xl_attachments WHERE attachment_id=?", (cursor,)).fetchone()
                        if (not binding or binding["row_id"] != source["row_id"]
                                or binding["object_hash"] != source["object_hash"]
                                or current["_revision"] != source["row_revision"] or table["path"] != source["path"]):
                            skipped += 1
                            continue
                        remove_references(db, tid, attachment_id=cursor)
                        payload = {
                            "table_id": tid, "row_id": source["row_id"], "row_revision": source["row_revision"],
                            "attachment_id": cursor, "object_hash": source["object_hash"], "path": table["path"],
                            "point_id": point_id, "space_key": space.key(), **space.model_dump(),
                        }
                        client.upsert([{"id": point_id, "vector": {"vision": vector}, "payload": payload}])
                        db.execute("INSERT INTO __xl_vision_refs VALUES(?,?,?,?,?,?,?,?,?)", (
                            point_id, cursor, source["row_id"], source["row_revision"], source["object_hash"],
                            table["path"], space.collection(), space.model_dump_json(), time.time(),
                        ))
                    indexed += 1
                jobs.update(job_id, indexed=indexed, skipped=skipped)
            jobs.update(job_id, status="completed", indexed=indexed, skipped=skipped)
            record("index", "ok")
        except APIError as error:
            jobs.update(job_id, status="failed", error=error.message, error_code=error.code, indexed=indexed, skipped=skipped)
            record("index", "error", error.code)
            logger.warning("vision index failed job=%s code=%s", job_id, error.code)
        except Exception:
            jobs.update(job_id, status="failed", error="vision indexing failed", error_code="VISION_INDEX_FAILED")
            record("index", "error", "VISION_INDEX_FAILED")
            logger.warning("vision index failed job=%s code=VISION_INDEX_FAILED", job_id)
        finally:
            if client:
                client.close()
            if provider:
                provider.close()
            index_lock.release()

    @app.post(
        "/api/v1/index/vision/reindex", status_code=202, response_model=VisionJobResponse,
        summary="Rebuild image attachment indexes", responses=VISION_ERRORS,
        description="Create an owner-scoped durable vision-only job. Requires xelibrary:admin and real Qwen/Qdrant.",
    )
    def reindex(body: VisionIndexRequest, request: Request, background: BackgroundTasks,
                actor=Depends(scope("xelibrary:admin"))):
        authorize(actor, body.table_id, "read")
        provider = VisionProvider()
        try:
            space = provider.space()
            client = VisionAdapter(space)
            try:
                client.ensure_collection()
            finally:
                client.close()
        finally:
            provider.close()
        from uuid import uuid4

        job_id = str(uuid4())
        with table_transaction(body.table_id):
            jobs.create(job_id, actor.sub, body.table_id)
            jobs.update(job_id, kind="vision", space=space.model_dump())
        background.add_task(run, job_id, body.table_id, actor, space)
        return response(request, {"job_id": job_id, "space": space.model_dump()})

    def coverage(actor, query, space):
        """Bounded current-binding coverage; no counts from denied rows/tables."""
        missing, scanned, truncated = 0, 0, False
        with connect(catalogue) as db:
            table_ids = [row[0] for row in db.execute("SELECT id FROM tables ORDER BY id")]
        for tid in table_ids:
            try:
                table = authorize(actor, tid, "read")
                if not path_matches(table["path"], query.path):
                    continue
                permits = request_acl(actor, table)
                with table_connect(tid) as db:
                    exists = references_exist(db)
                    for attachment in db.execute(
                        "SELECT a.* FROM __xl_attachments a JOIN data d ON d._row_id=a.row_id "
                        "WHERE d._deleted_at IS NULL ORDER BY a.attachment_id",
                    ):
                        if not permits("read", attachment["row_id"]):
                            continue
                        row = load_row(db, attachment["row_id"])
                        if any(row["values"].get(k) != v for k, v in query.filters.items()):
                            continue
                        # Legacy uploads may have generic MIME and arbitrary
                        # filenames. Inspect only a bounded CAS header, after ACL.
                        if not attachment_is_image(attachment):
                            continue
                        scanned += 1
                        if scanned > 10000:
                            truncated = True
                            break
                        indexed = db.execute(
                            "SELECT 1 FROM __xl_vision_refs WHERE attachment_id=? AND row_revision=? "
                            "AND object_hash=? AND path=? AND collection=? AND space=?",
                            (attachment["attachment_id"], row["_revision"], attachment["object_hash"], table["path"],
                             space.collection(), space.model_dump_json()),
                        ).fetchone() if exists else None
                        missing += int(not indexed)
            except APIError as error:
                if error.status not in {403, 404}:
                    raise
            if truncated:
                break
        return missing, truncated

    def perform_search(actor, request, query, raw, attachment_id, table_id):
        started = time.monotonic()
        provider, client, source = None, None, None
        try:
            if attachment_id:
                actor.require("xelibrary:read")
                source = resolve_attachment(actor, table_id, attachment_id)
                raw = source["payload"]
            if raw is not None:
                validate_image(raw)
            provider = VisionProvider()
            space = provider.space()
            vector = provider.encode(space, payload=raw, text=query.query, purpose="query")
            client = VisionAdapter(space)
            candidate_limit = min(max(query.limit * 10, 100), 1000)
            candidates = client.query(vector, candidate_limit)
            missing, truncated = coverage(actor, query, space)
            hits, stale = {}, 0
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                payload, score = candidate.get("payload"), candidate.get("score")
                if (not isinstance(payload, dict) or isinstance(score, bool) or not isinstance(score, (int, float))
                        or not math.isfinite(score) or not -1.000001 <= score <= 1.000001):
                    continue
                if (payload.get("space_key") != space.key() or payload.get("point_id") != candidate.get("id")
                        or any(payload.get(k) != v for k, v in space.model_dump().items())):
                    continue
                tid, rid, aid = payload.get("table_id"), payload.get("row_id"), payload.get("attachment_id")
                if not all(isinstance(value, str) for value in (tid, rid, aid)):
                    continue
                try:
                    table = authorize(actor, tid, "read", rid)
                    if not path_matches(table["path"], query.path):
                        continue
                    with table_connect(tid) as db:
                        row = load_row(db, rid)
                        binding = db.execute("SELECT * FROM __xl_attachments WHERE attachment_id=? AND row_id=?",
                                             (aid, rid)).fetchone()
                        if (not binding or binding["object_hash"] != payload.get("object_hash")
                                or row["_revision"] != payload.get("row_revision") or table["path"] != payload.get("path")
                                or not references_exist(db)):
                            stale += 1
                            continue
                        ref = db.execute("SELECT * FROM __xl_vision_refs WHERE point_id=? AND attachment_id=?",
                                         (candidate["id"], aid)).fetchone()
                        if (not ref or ref["space"] != space.model_dump_json() or ref["collection"] != space.collection()
                                or ref["object_hash"] != binding["object_hash"] or ref["row_revision"] != row["_revision"]
                                or ref["row_id"] != rid or ref["path"] != table["path"]):
                            stale += 1
                            continue
                        if any(row["values"].get(k) != v for k, v in query.filters.items()):
                            continue
                        # Current ACL/path check while the source binding remains
                        # protected by the stable table lock.
                        if authorize(actor, tid, "read", rid)["path"] != table["path"]:
                            continue
                        snippet = next((v for v in row["values"].values() if isinstance(v, str)), binding["filename"])
                        hit = VisionHit(
                            table_id=tid, row_id=rid, row_revision=row["_revision"], path=table["path"],
                            score=min(1.0, max(-1.0, score)), snippet=snippet[:240],
                            matched_attachments=[{"attachment_id": aid, "object_hash": binding["object_hash"]}],
                            indexed_revision=row["_revision"],
                        ).model_dump()
                    hit["_point_id"] = candidate["id"]
                    key = (tid, rid)
                    if key not in hits or hit["score"] > hits[key]["score"]:
                        hits[key] = hit
                except APIError as error:
                    if error.status not in {403, 404}:
                        raise
            # Earlier candidates may have become inaccessible during later
            # work. Recheck the whole accepted set at the response boundary.
            fresh = []
            for hit in hits.values():
                tid, rid = hit["table_id"], hit["row_id"]
                matched = hit["matched_attachments"][0]
                try:
                    table = authorize(actor, tid, "read", rid)
                    with table_connect(tid) as db:
                        row = load_row(db, rid)
                        binding = db.execute(
                            "SELECT * FROM __xl_attachments WHERE attachment_id=? AND row_id=?",
                            (matched["attachment_id"], rid),
                        ).fetchone()
                        ref = db.execute("SELECT * FROM __xl_vision_refs WHERE point_id=?",
                                         (hit["_point_id"],)).fetchone() if references_exist(db) else None
                        if (not binding or not ref or row["_revision"] != hit["row_revision"]
                                or table["path"] != hit["path"] or not path_matches(table["path"], query.path)
                                or binding["object_hash"] != matched["object_hash"]
                                or ref["attachment_id"] != matched["attachment_id"] or ref["row_id"] != rid
                                or ref["object_hash"] != binding["object_hash"] or ref["row_revision"] != row["_revision"]
                                or ref["path"] != table["path"] or ref["space"] != space.model_dump_json()
                                or ref["collection"] != space.collection()
                                or any(row["values"].get(k) != v for k, v in query.filters.items())
                                or authorize(actor, tid, "read", rid)["path"] != table["path"]):
                            stale += 1
                            continue
                        snippet = next((v for v in row["values"].values() if isinstance(v, str)), binding["filename"])
                        hit["snippet"] = snippet[:240]
                        del hit["_point_id"]
                        fresh.append(hit)
                except APIError as error:
                    if error.status not in {403, 404}:
                        raise
                    stale += 1
            # A source attachment is also caller data. Worker wait must not let
            # an unbound or newly denied source silently complete its query.
            if source is not None:
                current_source = resolve_attachment(actor, table_id, attachment_id, include_payload=False)
                if (current_source["object_hash"] != source["object_hash"]
                        or current_source["row_id"] != source["row_id"]):
                    raise APIError(409, "STALE_ATTACHMENT", "query source attachment changed")
            ordered = sorted(fresh, key=lambda hit: (-hit["score"], hit["table_id"], hit["row_id"]))[:query.limit]
            record("search", "ok")
            return response(request, ordered, partial=bool(missing or stale or truncated or len(candidates) >= candidate_limit),
                            missing_embeddings=missing, index_backend="qdrant_vision", space=space.model_dump(),
                            candidate_limit=candidate_limit, coverage_truncated=truncated, coverage_snapshot=True)
        except APIError as error:
            record("search", "error", error.code)
            logger.warning("vision search failed request=%s code=%s", request.state.request_id, error.code)
            raise
        finally:
            metrics.observe("xelibrary_vision_latency_seconds", time.monotonic() - started,
                            help="Vision search latency.", operation="search")
            if client:
                client.close()
            if provider:
                provider.close()

    @app.post(
        "/api/v1/search/image", response_model=VisionSearchResponse, summary="Search the Qwen vision index", responses=VISION_ERRORS,
        description="Choose exactly one multipart upload, attachment_id plus table_id, or text. "
                    "Current ACL/binding/hash/revision checks apply; query and index use the same pinned Qwen space.",
    )
    async def image_search(
        request: Request, upload: UploadFile | None = File(None),
        attachment_id: str | None = Form(None, min_length=1, max_length=64), table_id: str | None = Form(None, min_length=1, max_length=64),
        text: str | None = Form(None, min_length=1, max_length=4096), path: str = Form("/**", max_length=1024),
        limit: int = Form(20, ge=1, le=100), filters: str = Form("{}", max_length=16384, description="JSON object of exact row field filters"),
        actor=Depends(scope("xelibrary:query")),
    ):
        if sum((upload is not None, attachment_id is not None, text is not None)) != 1:
            raise APIError(422, "VALIDATION_ERROR", "choose exactly one upload, attachment reference or text")
        if bool(attachment_id) != bool(table_id) or (text is not None and not text.strip()):
            raise APIError(422, "VALIDATION_ERROR", "attachment needs table_id; text cannot be blank")
        try:
            parsed = json.loads(filters)
            if not isinstance(parsed, dict):
                raise ValueError
        except ValueError:
            raise APIError(422, "VALIDATION_ERROR", "filters must be a JSON object") from None
        query = Search(query=text or "image", path=path, limit=limit, filters=parsed)
        raw = await upload.read(IMAGE_MAX_BYTES + 1) if upload else None
        return await run_in_threadpool(perform_search, actor, request, query, raw, attachment_id, table_id)
