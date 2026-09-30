"""Single-node exact-vector bootstrap index; facts always revalidated in .xl."""

import json
import os
import threading
from uuid import uuid4

import httpx
from fastapi import BackgroundTasks, Depends, Request
from pydantic import BaseModel

from .common import APIError, connect, response, scope, transaction
from .jobs import JobStore
from .query import path_matches
from .services import Search


class IndexRequest(BaseModel):
    table_id: str


def install(app, catalogue, file, authorize, allowed, load_row):
    lock = threading.Lock()
    jobs = JobStore(catalogue)
    endpoint = os.getenv("EMBEDDING_URL")
    credential = os.getenv("EMBEDDING_SERVICE_TOKEN") or os.getenv("EMBEDDING_CLIENT_SECRET_FILE")
    tokenizer_path = os.getenv("D100M_TOKENIZER_PATH")

    def configured():
        if not endpoint or not credential or not tokenizer_path:
            raise APIError(
                503, "SEARCH_NOT_CONFIGURED", "embedding URL, service token and D100M tokenizer required"
            )

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

    def chunks(values, path):
        from tokenizers import Tokenizer

        tokenizer = Tokenizer.from_file(tokenizer_path)
        for column, value in values.items():
            if not isinstance(value, str) or not value.strip():
                continue
            prefix = tokenizer.encode(f"[PATH] {path} [COLUMN] {column} [TEXT] ").ids[:18]
            tokens = tokenizer.encode(value).ids
            budget = 108
            for offset in range(0, len(tokens), budget - 16):
                chunk = tokenizer.decode(prefix + tokens[offset : offset + budget])
                # Decode/reencode may change token boundaries; enforce before inference.
                encoded = tokenizer.encode(chunk).ids
                yield column, tokenizer.decode(encoded[:126])
                if offset + budget >= len(tokens):
                    break

    def initialize(tid):
        with transaction(file(tid)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS __xl_vectors(row_id TEXT,revision INTEGER,
                       chunk_id TEXT PRIMARY KEY,column_name TEXT,body TEXT,vector TEXT,model_revision TEXT)""")

    def run(job_id, tid, actor):
        if not lock.acquire(blocking=False):
            jobs.update(job_id, status="failed", error="index worker busy")
            return
        jobs.update(job_id, status="running")
        try:
            table = authorize(actor, tid, "read")
            initialize(tid)
            with connect(file(tid)) as db:
                ids = [r[0] for r in db.execute("SELECT _row_id FROM data WHERE _deleted_at IS NULL")]
            for rid in ids:
                if not allowed(actor, table, "read", rid):
                    continue
                with connect(file(tid)) as db:
                    row = load_row(db, rid)
                encoded = []
                for column, chunk in chunks(row["values"], table["path"]):
                    vectors, revision = embedding([chunk])
                    encoded.append(
                        (rid, row["_revision"], str(uuid4()), column, chunk, json.dumps(vectors[0]), revision)
                    )
                with transaction(file(tid)) as db:
                    current = load_row(db, rid)
                    if current["_revision"] != row["_revision"]:
                        continue
                    db.execute("DELETE FROM __xl_vectors WHERE row_id=?", (rid,))
                    db.executemany("INSERT INTO __xl_vectors VALUES(?,?,?,?,?,?,?)", encoded)
                    db.execute(
                        "UPDATE __xl_changes SET indexed_revision=? WHERE row_id=? AND revision<=?",
                        (row["_revision"], rid, row["_revision"]),
                    )
            jobs.update(job_id, status="completed")
        except Exception:
            jobs.update(job_id, status="failed", error="indexing failed; check embedding dependency")
        finally:
            lock.release()

    @app.post("/api/v1/index/reindex", status_code=202)
    def reindex(
        body: IndexRequest,
        request: Request,
        background: BackgroundTasks,
        actor=Depends(scope("xelibrary:admin")),
    ):
        configured()
        authorize(actor, body.table_id, "read")
        job_id = str(uuid4())
        jobs.create(job_id, actor.sub, body.table_id)
        background.add_task(run, job_id, body.table_id, actor)
        return response(request, {"job_id": job_id})

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str, request: Request, actor=Depends(scope("xelibrary:admin"))):
        data = jobs.get(job_id)
        if data is None:
            raise APIError(404, "JOB_NOT_FOUND", "job not found")
        if data["owner"] != actor.sub:
            raise APIError(403, "FORBIDDEN", "job belongs to another caller")
        return response(request, data)

    @app.post("/api/v1/search/semantic")
    def semantic(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
        configured()
        if body.mode == "quality":
            raise APIError(503, "QWEN_UNAVAILABLE", "quality worker not configured")
        vectors, model_revision = embedding([body.query])
        query = vectors[0]
        with connect(catalogue) as db:
            tables = [dict(t) for t in db.execute("SELECT * FROM tables")]
        results, missing = [], 0
        for table in tables:
            if not path_matches(table["path"], body.path) or not allowed(actor, table, "read"):
                continue
            initialize(table["id"])
            with connect(file(table["id"])) as db:
                for raw in db.execute("SELECT _row_id FROM data WHERE _deleted_at IS NULL"):
                    rid = raw[0]
                    if not allowed(actor, table, "read", rid):
                        continue
                    row = load_row(db, rid)
                    if any(row["values"].get(k) != v for k, v in body.filters.items()):
                        continue
                    hits = db.execute(
                        "SELECT * FROM __xl_vectors WHERE row_id=? AND revision=? AND model_revision=?",
                        (rid, row["_revision"], model_revision),
                    ).fetchall()
                    if not hits:
                        missing += 1
                        continue
                    best = max(hits, key=lambda h: sum(x * y for x, y in zip(query, json.loads(h["vector"]))))
                    # Current row state, revision and ACL checked after candidate retrieval as well.
                    current = load_row(db, rid)
                    if current["_revision"] != row["_revision"] or not allowed(actor, table, "read", rid):
                        continue
                    results.append(
                        {
                            "table_id": table["id"],
                            "row_id": rid,
                            "row_revision": current["_revision"],
                            "path": table["path"],
                            "score": sum(x * y for x, y in zip(query, json.loads(best["vector"]))),
                            "score_type": "d100m_cosine",
                            "snippet": best["body"][:240],
                            "matched_chunks": [{"chunk_id": best["chunk_id"], "column": best["column_name"]}],
                            "approximate": False,
                            "indexed_revision": best["revision"],
                        }
                    )
        results.sort(key=lambda r: r["score"], reverse=True)
        return response(
            request,
            results[: body.limit],
            partial=bool(missing),
            missing_embeddings=missing,
            index_backend="sqlite_exact_bootstrap",
            model_revision=model_revision,
        )
