"""Durable core controls for capabilities whose invocation paths enforce them.

No runtime loading or deployment. State is read through the existing live account
identity authority, never through plugin-supplied endpoints or bearer claims.
"""

import sqlite3
import time
from uuid import UUID

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from .common import APIError, connect, identity, response, transaction

QUERY_CAPABILITY = "builtin.platform.tool.xelibrary-query"
# Expand only alongside an actual invocation + discovery integration and tests.
LLM_CAPABILITY = "builtin.platform.provider.llm-chat"
D100M_CAPABILITY = "builtin.platform.provider.d100m"
QWEN_CAPABILITY = "builtin.platform.provider.qwen-worker"
INDEX_CAPABILITY = "builtin.platform.worker.index"
CONTROLLABLE = frozenset({QUERY_CAPABILITY, LLM_CAPABILITY, D100M_CAPABILITY, QWEN_CAPABILITY, INDEX_CAPABILITY})
CAPABILITY_PARENTS = {
    "builtin.platform.worker.document-chunker": INDEX_CAPABILITY,
    D100M_CAPABILITY + "-encode": D100M_CAPABILITY,
    QWEN_CAPABILITY + "-encode": QWEN_CAPABILITY,
}


def capability_disabled(capability_id, disabled):
    return capability_id in disabled or CAPABILITY_PARENTS.get(capability_id) in disabled

MAX_GENERATION = 2**63 - 1


def parse_snapshot(value):
    if not isinstance(value, dict) or set(value) != {"version", "revision", "disabled"}:
        raise ValueError("invalid lifecycle snapshot")
    revision, disabled = value["revision"], value["disabled"]
    if type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unknown lifecycle version")
    if type(revision) is not int or not 0 <= revision <= MAX_GENERATION:
        raise ValueError("invalid lifecycle revision")
    if not isinstance(disabled, list) or len(disabled) > len(CONTROLLABLE):
        raise ValueError("invalid lifecycle disabled set")
    if any(not isinstance(item, str) or item not in CONTROLLABLE for item in disabled):
        raise ValueError("unsupported controlled capability")
    if len(set(disabled)) != len(disabled) or (not revision and disabled):
        raise ValueError("invalid lifecycle disabled set")
    return revision, frozenset(disabled)


class LifecyclePatch(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool
    expected_generation: int = Field(ge=0, le=MAX_GENERATION)
    operation_id: UUID = Field(strict=False)


class CapabilityLifecycle:
    def __init__(self, path):
        self.path = path
        with transaction(path) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS capability_lifecycle_meta(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL CHECK(revision>=0));
                INSERT OR IGNORE INTO capability_lifecycle_meta VALUES(1,0);
                CREATE TABLE IF NOT EXISTS capability_lifecycle(
                    capability_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
                    generation INTEGER NOT NULL CHECK(generation>0));
                CREATE TABLE IF NOT EXISTS capability_lifecycle_audit(
                    actor TEXT NOT NULL, operation_id TEXT NOT NULL, capability_id TEXT NOT NULL,
                    enabled INTEGER NOT NULL, expected_generation INTEGER NOT NULL,
                    applied_generation INTEGER NOT NULL, at REAL NOT NULL,
                    PRIMARY KEY(actor,operation_id));
            """)

    def snapshot(self):
        try:
            with connect(self.path) as db:
                # A read transaction binds revision and controls to one snapshot.
                db.execute("BEGIN")
                row = db.execute("SELECT revision FROM capability_lifecycle_meta WHERE singleton=1").fetchone()
                if row is None:
                    raise ValueError
                rows = db.execute("SELECT * FROM capability_lifecycle").fetchall()
                if any(r["capability_id"] not in CONTROLLABLE or r["enabled"] not in (0, 1)
                       or type(r["generation"]) is not int or not 0 < r["generation"] <= row["revision"] for r in rows):
                    raise ValueError
                snapshot = {"version": 1, "revision": row["revision"],
                            "disabled": sorted(r["capability_id"] for r in rows if not r["enabled"])}
                parse_snapshot(snapshot)
                return snapshot
        except (sqlite3.Error, ValueError, TypeError):
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "capability lifecycle state unavailable") from None

    @staticmethod
    def _controlled(capability_id):
        if capability_id not in CONTROLLABLE:
            raise APIError(404, "CAPABILITY_NOT_CONTROLLABLE", "capability invocation is not lifecycle controlled")

    @staticmethod
    def _state(db, capability_id):
        row = db.execute("SELECT * FROM capability_lifecycle WHERE capability_id=?", (capability_id,)).fetchone()
        return {"capability_id": capability_id, "enabled": bool(row["enabled"]) if row else True,
                "generation": row["generation"] if row else 0}

    def state(self, capability_id):
        self._controlled(capability_id)
        self.snapshot()  # Fail closed on inconsistent persisted state.
        try:
            with connect(self.path) as db:
                return self._state(db, capability_id)
        except sqlite3.Error:
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "capability lifecycle state unavailable") from None

    def change(self, capability_id, body, actor):
        self._controlled(capability_id)
        self.snapshot()
        with transaction(self.path) as db:
            old = db.execute("SELECT * FROM capability_lifecycle_audit WHERE actor=? AND operation_id=?",
                             (actor, str(body.operation_id))).fetchone()
            if old:
                if (old["capability_id"], bool(old["enabled"]), old["expected_generation"]) != (
                    capability_id, body.enabled, body.expected_generation,
                ):
                    raise APIError(409, "LIFECYCLE_OPERATION_CONFLICT", "operation id reused with different input")
                return {**self._state(db, capability_id), "applied_generation": old["applied_generation"],
                        "duplicate": True}
            current = self._state(db, capability_id)
            if current["generation"] != body.expected_generation:
                raise APIError(409, "LIFECYCLE_GENERATION_CONFLICT", "capability generation changed")
            revision = db.execute("SELECT revision FROM capability_lifecycle_meta WHERE singleton=1").fetchone()[0]
            if revision >= MAX_GENERATION:
                raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "capability generation exhausted")
            revision += 1
            db.execute("UPDATE capability_lifecycle_meta SET revision=? WHERE singleton=1", (revision,))
            db.execute("INSERT INTO capability_lifecycle VALUES(?,?,?) ON CONFLICT(capability_id) DO UPDATE SET "
                       "enabled=excluded.enabled,generation=excluded.generation",
                       (capability_id, int(body.enabled), revision))
            db.execute("INSERT INTO capability_lifecycle_audit VALUES(?,?,?,?,?,?,?)",
                       (actor, str(body.operation_id), capability_id, int(body.enabled),
                        body.expected_generation, revision, time.time()))
            return {**self._state(db, capability_id), "applied_generation": revision, "duplicate": False}


def install(app):
    store = CapabilityLifecycle(app.state.settings.data_dir / "accounts.sqlite")
    app.state.capability_lifecycle = store

    def manager(request: Request, actor=Depends(identity)):
        actor.require("capabilities:manage")
        if actor.kind != "human" or request.headers.get("Authorization", "").startswith("Bearer xek_"):
            raise APIError(403, "INTERACTIVE_LOGIN_REQUIRED", "human OIDC login required for lifecycle changes")
        if not app.state.capability_lifecycle_required:
            raise APIError(503, "CAPABILITY_LIFECYCLE_NOT_ENABLED", "lifecycle control rollout is not enabled")
        return actor

    def controlled(capability_id):
        if capability_id not in CONTROLLABLE:
            raise APIError(404, "CAPABILITY_NOT_CONTROLLABLE", "capability invocation is not lifecycle controlled")

    @app.get("/api/v1/capabilities/{capability_id}/lifecycle")
    def get_state(capability_id: str, request: Request, actor=Depends(manager)):
        controlled(capability_id)
        return response(request, store.state(capability_id))

    @app.patch("/api/v1/capabilities/{capability_id}/lifecycle", responses={
        409: {"description": "Stale generation or operation id reused with different input"},
        503: {"description": "Lifecycle rollout disabled or authority state unavailable"},
    })
    def change_state(capability_id: str, body: LifecyclePatch, request: Request, actor=Depends(manager)):
        controlled(capability_id)
        try:
            return response(request, store.change(capability_id, body, actor.sub))
        except sqlite3.Error:
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "capability lifecycle state unavailable") from None
