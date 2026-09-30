"""Theorem data model and API (plan.md §15 and §34 data plane).

Theorems live in the XeLibrary catalogue with revision history, explicit
formal/review status separation and provenance. Formal verification and
candidate generation belong to the Library Agent and Lean sandbox; those
endpoints fail closed with 503 until those services exist instead of
pretending to verify anything.
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .common import APIError, connect, etag, precondition, response, scope, transaction

FORMAL_STATUSES = ("none", "proposed", "generated", "verifying", "verified", "failed", "invalidated")
REVIEW_STATUSES = ("draft", "proposed", "promoted", "rejected")
FORMAL_INVALID = ("failed", "invalidated")


class Confidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_score: float = Field(default=0.0, ge=0.0, le=1.0)
    source_score: float = Field(default=0.0, ge=0.0, le=1.0)
    consistency_score: float = Field(default=0.0, ge=0.0, le=1.0)


class TheoremWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(min_length=2, max_length=1024)
    title: str = Field(min_length=1, max_length=300)
    statement_natural: str = Field(min_length=1, max_length=20000)
    assumptions: list[str] = Field(default_factory=list, max_length=64)
    proof_natural: str = Field(default="", max_length=200000)
    formal_statement: str = Field(default="", max_length=200000)
    lean_source: str = Field(default="", max_length=500000)
    lean_version: str = Field(default="", max_length=80)
    dependency_lock: str = Field(default="", max_length=20000)
    depends_on: list[str] = Field(default_factory=list, max_length=64)
    evidence_refs: list[str] = Field(default_factory=list, max_length=64)
    formal_status: str = "none"
    confidence: Confidence = Field(default_factory=Confidence)
    provenance: str = Field(default="", max_length=4000)
    review_status: str = "proposed"

    @field_validator("path")
    @classmethod
    def valid_path(cls, value):
        if (
            not value.startswith("/theorems")
            or (value != "/theorems" and not value.startswith("/theorems/"))
            or ".." in value.split("/")
            or any(ord(character) < 32 for character in value)
        ):
            raise ValueError("theorem path must live under /theorems/")
        return "/" + "/".join(part for part in value.split("/") if part and part != ".")

    @field_validator("formal_status")
    @classmethod
    def valid_formal_status(cls, value):
        if value not in FORMAL_STATUSES:
            raise ValueError("unsupported formal_status")
        return value

    @field_validator("review_status")
    @classmethod
    def valid_review_status(cls, value):
        if value not in REVIEW_STATUSES:
            raise ValueError("unsupported review_status")
        return value

    @field_validator("depends_on", "evidence_refs")
    @classmethod
    def unique_entries(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("duplicate entries")
        return value


class TheoremPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str | None = None
    title: str | None = None
    statement_natural: str | None = None
    assumptions: list[str] | None = None
    proof_natural: str | None = None
    formal_statement: str | None = None
    lean_source: str | None = None
    lean_version: str | None = None
    dependency_lock: str | None = None
    depends_on: list[str] | None = None
    evidence_refs: list[str] | None = None
    formal_status: str | None = None
    confidence: Confidence | None = None
    provenance: str | None = None
    review_status: str | None = None


class EvidenceAppend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refs: list[str] = Field(min_length=1, max_length=64)


class TheoremSearch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=256)
    path: str = Field(default="/theorems/**", max_length=1024)
    review_status: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


def install(app, catalogue, allowed, audit):
    # The Prometheus registry arrives with the metrics increment (issue #30);
    # theorem endpoints work with or without it.
    metrics = getattr(app.state, "metrics", None)
    with transaction(catalogue) as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS theorems(id TEXT PRIMARY KEY,payload TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 1,created_by TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE IF NOT EXISTS theorem_history(id TEXT NOT NULL,revision INTEGER NOT NULL,
          payload TEXT NOT NULL,actor TEXT,at TEXT,PRIMARY KEY(id,revision));
        """)

    def stamp():
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def load(db, theorem_id):
        row = db.execute("SELECT * FROM theorems WHERE id=?", (theorem_id,)).fetchone()
        if row is None:
            raise APIError(404, "THEOREM_NOT_FOUND", "theorem not found")
        return {
            "theorem_id": row["id"],
            **json.loads(row["payload"]),
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "revision": row["revision"],
        }

    def stub(theorem):
        return {
            "id": f"theorem:{theorem['theorem_id']}",
            "path": theorem["path"],
            "owner": theorem["created_by"],
        }

    def visible(actor, theorem):
        return allowed(actor, stub(theorem), "read")

    def visible_or_404(actor, theorem):
        if not visible(actor, theorem):
            raise APIError(404, "THEOREM_NOT_FOUND", "theorem not found")
        return theorem

    def writable(actor, theorem):
        if not allowed(actor, stub(theorem), "write"):
            raise APIError(403, "THEOREM_READ_ONLY", "write permission denied")

    def write(db, theorem_id, payload, actor, created=None, created_at=None):
        now = stamp()
        previous = db.execute("SELECT * FROM theorems WHERE id=?", (theorem_id,)).fetchone()
        revision = (previous["revision"] + 1) if previous else 1
        db.execute(
            "INSERT OR REPLACE INTO theorems VALUES(?,?,?,?,?,?)",
            (
                theorem_id,
                json.dumps(payload),
                revision,
                (previous["created_by"] if previous else created),
                (previous["created_at"] if previous else created_at),
                now,
            ),
        )
        db.execute(
            "INSERT OR REPLACE INTO theorem_history VALUES(?,?,?,?,?)",
            (theorem_id, revision, json.dumps(payload), actor.sub, now),
        )

    def dependency_cycle(db, theorem_id, depends_on):
        graph = {}
        for row in db.execute("SELECT id,payload FROM theorems"):
            graph[row["id"]] = json.loads(row["payload"]).get("depends_on", [])
        graph[theorem_id] = list(depends_on)
        seen, stack = set(), list(depends_on)
        while stack:
            node = stack.pop()
            if node == theorem_id:
                return True
            if node in seen:
                continue
            seen.add(node)
            stack.extend(graph.get(node, []))
        return False

    def resolve(db, ids):
        summaries = []
        for theorem_id in ids:
            row = db.execute("SELECT * FROM theorems WHERE id=?", (theorem_id,)).fetchone()
            if row is None:
                summaries.append({"theorem_id": theorem_id, "missing": True})
                continue
            payload = json.loads(row["payload"])
            summaries.append(
                {
                    "theorem_id": theorem_id,
                    "title": payload["title"],
                    "path": payload["path"],
                    "formal_status": payload["formal_status"],
                    "review_status": payload["review_status"],
                    "missing": False,
                }
            )
        return summaries

    @app.get("/api/v1/theorems")
    def list_theorems(
        request: Request,
        path: str = "/theorems/**",
        review_status: str = "",
        formal_status: str = "",
        limit: int = 50,
        cursor: str = "",
        actor=Depends(scope("xelibrary:theorem:read")),
    ):
        from .query import path_matches

        if not 1 <= limit <= 200:
            raise APIError(422, "INVALID_LIMIT", "limit outside range")
        if review_status and review_status not in REVIEW_STATUSES:
            raise APIError(422, "INVALID_STATUS", "unsupported review_status")
        if formal_status and formal_status not in FORMAL_STATUSES:
            raise APIError(422, "INVALID_STATUS", "unsupported formal_status")
        with connect(catalogue) as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM theorems WHERE id>? ORDER BY id", (cursor,))]
            rows = [load(db, theorem_id) for theorem_id in ids]
        selected = [
            theorem
            for theorem in rows
            if path_matches(theorem["path"], path)
            and visible(actor, theorem)
            and (not review_status or theorem["review_status"] == review_status)
            and (not formal_status or theorem["formal_status"] == formal_status)
        ]
        page = selected[: limit + 1]
        return response(
            request,
            page[:limit],
            next_cursor=page[limit - 1]["theorem_id"] if len(page) > limit else None,
        )

    @app.post("/api/v1/theorems", status_code=201)
    def create_theorem(body: TheoremWrite, request: Request, actor=Depends(scope("xelibrary:theorem:propose"))):
        theorem_id = str(uuid4())
        payload = body.model_dump()
        with transaction(catalogue) as db:
            if dependency_cycle(db, theorem_id, body.depends_on):
                raise APIError(422, "THEOREM_CYCLE", "dependency cycle or self reference")
            write(db, theorem_id, payload, actor, created=actor.sub, created_at=stamp())
            theorem = load(db, theorem_id)
        audit(actor, "theorem.created", theorem_id)
        return JSONResponse(
            response(request, theorem), status_code=201, headers={"ETag": etag(theorem["revision"])}
        )

    @app.get("/api/v1/theorems/{theorem_id}")
    def get_theorem(theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:read"))):
        with connect(catalogue) as db:
            theorem = visible_or_404(actor, load(db, theorem_id))
        return JSONResponse(response(request, theorem), headers={"ETag": etag(theorem["revision"])})

    @app.patch("/api/v1/theorems/{theorem_id}")
    def patch_theorem(
        theorem_id: str,
        body: TheoremPatch,
        request: Request,
        actor=Depends(scope("xelibrary:theorem:propose")),
    ):
        with transaction(catalogue) as db:
            current = visible_or_404(actor, load(db, theorem_id))
            writable(actor, current)
            precondition(request, current["revision"])
            if current["review_status"] == "promoted" and "xelibrary:theorem:promote" not in actor.scopes:
                raise APIError(403, "THEOREM_PROMOTED", "promoted theorems require theorem:promote")
            merged = {key: value for key, value in current.items() if key not in ("theorem_id",)}
            merged.pop("created_by", None)
            merged.pop("created_at", None)
            merged.pop("updated_at", None)
            merged.pop("revision", None)
            merged.update(body.model_dump(exclude_unset=True))
            try:
                updated = TheoremWrite.model_validate(merged)
            except ValueError:
                raise APIError(422, "INVALID_THEOREM", "invalid theorem fields") from None
            if dependency_cycle(db, theorem_id, updated.depends_on):
                raise APIError(422, "THEOREM_CYCLE", "dependency cycle or self reference")
            write(db, theorem_id, updated.model_dump(), actor)
            theorem = load(db, theorem_id)
        audit(actor, "theorem.updated", theorem_id)
        return JSONResponse(response(request, theorem), headers={"ETag": etag(theorem["revision"])})

    @app.get("/api/v1/theorems/{theorem_id}/revisions")
    def theorem_revisions(
        theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:read"))
    ):
        with connect(catalogue) as db:
            visible_or_404(actor, load(db, theorem_id))
            rows = db.execute(
                "SELECT revision,payload,actor,at FROM theorem_history WHERE id=? ORDER BY revision",
                (theorem_id,),
            ).fetchall()
        return response(
            request,
            [{"revision": row["revision"], "actor": row["actor"], "at": row["at"], **json.loads(row["payload"])} for row in rows],
        )

    @app.post("/api/v1/theorems/{theorem_id}/promote")
    def promote_theorem(
        theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:promote"))
    ):
        with transaction(catalogue) as db:
            current = load(db, theorem_id)
            precondition(request, current["revision"])
            if current["review_status"] == "promoted":
                raise APIError(409, "THEOREM_PROMOTED", "theorem is already promoted")
            if current["review_status"] != "proposed":
                raise APIError(409, "THEOREM_NOT_PROPOSED", "only proposed theorems can be promoted")
            if current["formal_status"] in FORMAL_INVALID:
                raise APIError(409, "THEOREM_INVALID", "failed or invalidated theorems cannot be promoted")
            payload = {key: value for key, value in current.items() if key not in ("theorem_id", "created_by", "created_at", "updated_at", "revision")}
            payload["review_status"] = "promoted"
            write(db, theorem_id, payload, actor)
            theorem = load(db, theorem_id)
        audit(actor, "theorem.promoted", theorem_id)
        return JSONResponse(response(request, theorem), headers={"ETag": etag(theorem["revision"])})

    @app.get("/api/v1/theorems/{theorem_id}/dependencies")
    def dependencies(theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:read"))):
        with connect(catalogue) as db:
            theorem = visible_or_404(actor, load(db, theorem_id))
            result = resolve(db, theorem["depends_on"])
        return response(request, result)

    @app.get("/api/v1/theorems/{theorem_id}/dependents")
    def dependents(theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:read"))):
        with connect(catalogue) as db:
            visible_or_404(actor, load(db, theorem_id))
            ids = [row["id"] for row in db.execute("SELECT id FROM theorems ORDER BY id")]
            rows = [load(db, theorem_id) for theorem_id in ids]
            result = resolve(
                db,
                [
                    theorem["theorem_id"]
                    for theorem in rows
                    if theorem_id in theorem["depends_on"] and visible(actor, theorem)
                ],
            )
        return response(request, result)

    @app.get("/api/v1/theorems/{theorem_id}/evidence")
    def evidence(theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:read"))):
        with connect(catalogue) as db:
            theorem = visible_or_404(actor, load(db, theorem_id))
        return response(request, {"evidence_refs": theorem["evidence_refs"], "revision": theorem["revision"]})

    @app.post("/api/v1/theorems/{theorem_id}/evidence")
    def add_evidence(
        theorem_id: str,
        body: EvidenceAppend,
        request: Request,
        actor=Depends(scope("xelibrary:theorem:propose")),
    ):
        with transaction(catalogue) as db:
            current = visible_or_404(actor, load(db, theorem_id))
            writable(actor, current)
            precondition(request, current["revision"])
            payload = {
                key: value
                for key, value in current.items()
                if key not in ("theorem_id", "created_by", "created_at", "updated_at", "revision")
            }
            payload["evidence_refs"] = list(dict.fromkeys([*current["evidence_refs"], *body.refs]))
            write(db, theorem_id, payload, actor)
            theorem = load(db, theorem_id)
        audit(actor, "theorem.evidence", theorem_id)
        return JSONResponse(response(request, theorem), headers={"ETag": etag(theorem["revision"])})

    @app.post("/api/v1/theorems/search")
    def search_theorems(body: TheoremSearch, request: Request, actor=Depends(scope("xelibrary:theorem:read"))):
        from .query import path_matches

        if body.review_status and body.review_status not in REVIEW_STATUSES:
            raise APIError(422, "INVALID_STATUS", "unsupported review_status")
        needle = body.query.casefold()
        with connect(catalogue) as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM theorems ORDER BY id")]
            rows = [load(db, theorem_id) for theorem_id in ids]
        results = []
        for theorem in rows:
            if not path_matches(theorem["path"], body.path) or not visible(actor, theorem):
                continue
            if body.review_status and theorem["review_status"] != body.review_status:
                continue
            haystack = " ".join(
                [theorem["title"], theorem["statement_natural"], *theorem["assumptions"]]
            ).casefold()
            if needle not in haystack:
                continue
            results.append(
                {
                    "theorem_id": theorem["theorem_id"],
                    "path": theorem["path"],
                    "title": theorem["title"],
                    "formal_status": theorem["formal_status"],
                    "review_status": theorem["review_status"],
                    "confidence": theorem["confidence"],
                    "revision": theorem["revision"],
                }
            )
        return response(request, results[: body.limit], partial=len(results) > body.limit)

    @app.post("/api/v1/theorems/{theorem_id}/verify")
    def verify_theorem(
        theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:propose"))
    ):
        with connect(catalogue) as db:
            visible_or_404(actor, load(db, theorem_id))
        raise APIError(503, "LEAN_NOT_CONFIGURED", "Lean verification runs in the Library Agent phase")

    @app.post("/api/v1/theorems/{theorem_id}/candidates")
    def candidates(
        theorem_id: str, request: Request, actor=Depends(scope("xelibrary:theorem:propose"))
    ):
        with connect(catalogue) as db:
            visible_or_404(actor, load(db, theorem_id))
        raise APIError(503, "AGENT_NOT_CONFIGURED", "candidate generation requires the Library Agent")

    def theorem_counts():
        with connect(catalogue) as db:
            rows = [json.loads(row["payload"]) for row in db.execute("SELECT payload FROM theorems")]
        return [
            ({"review_status": status}, sum(1 for row in rows if row.get("review_status") == status))
            for status in REVIEW_STATUSES
        ]

    if metrics is not None:
        metrics.gauge_provider("xelibrary_theorems", "Theorems by review status.", theorem_counts)

    return app
