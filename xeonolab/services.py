import asyncio
import hashlib
import hmac
import json
import logging
import os
import ssl
from pathlib import Path

import httpx
from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, response, scope, transaction

SERVICES = [
    "xelibrary",
    "embedding",
    "hf-cache",
    "library-agent",
    "dev-agent",
    "tool-gateway",
    "llm-gateway",
    "deploy-gateway",
    "network-control",
    "desktop-web",
]


class Search(BaseModel):
    query: str = Field(min_length=1, max_length=4096)
    path: str = "/**"
    mode: str = Field(default="fast", pattern="^(fast|quality|auto)$")
    limit: int = Field(default=20, ge=1, le=100)
    filters: dict = Field(default_factory=dict)


class ToolQuery(BaseModel):
    expression: str


class ToolGet(BaseModel):
    table_id: str
    row_id: str
    fields: list[str] = Field(default_factory=list)


class Embed(BaseModel):
    model: str = "fast"
    input: str | list[str]


def create_app(service=None, settings=None):
    service = service or os.getenv("SERVICE", "xelibrary")
    if service == "xelibrary":
        from .library import create_app as factory

        return factory(settings)
    if service == "deploy-gateway":
        from .deploy import create_app as factory

        return factory(settings)
    if service == "desktop-web":
        from .desktop import create_app as factory

        return factory(settings)
    if service not in SERVICES:
        raise ValueError("unknown service")
    app = base_app(service, settings)

    if service == "embedding":
        from .embedding import create_app as factory

        return factory(settings)
    if service == "hf-cache":
        from .hf_cache import create_app as factory

        return factory(settings)

    if service == "tool-gateway":
        destination = os.getenv("XELIBRARY_URL", "http://xelibrary:8000")

        async def forward(request, method, path, payload=None):
            async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                result = await client.request(
                    method,
                    destination + path,
                    json=payload,
                    headers={
                        "Authorization": request.headers["Authorization"],
                        "X-Request-ID": request.state.request_id,
                    },
                )
            from fastapi.responses import JSONResponse

            return JSONResponse(result.json(), status_code=result.status_code)

        @app.get("/api/v1/tools")
        def tools(request: Request, actor=Depends(scope("xelibrary:query"))):
            return response(
                request,
                [
                    {
                        "type": "function",
                        "function": {
                            "name": name,
                            "description": description,
                            "parameters": model.model_json_schema(),
                        },
                    }
                    for name, description, model in [
                        ("xelibrary.search", "Search authorized knowledge", Search),
                        ("xelibrary.query", "Query authorized knowledge with DSL", ToolQuery),
                        ("xelibrary.get", "Read an authorized row", ToolGet),
                    ]
                ],
            )

        @app.post("/api/v1/tools/xelibrary.query")
        async def query(body: ToolQuery, request: Request, actor=Depends(scope("xelibrary:query"))):
            return await forward(request, "POST", "/api/v1/query", body.model_dump())

        @app.post("/api/v1/tools/xelibrary.get")
        async def get(body: ToolGet, request: Request, actor=Depends(scope("xelibrary:read"))):
            # UUID validation prevents tool input changing the upstream URL path.
            from uuid import UUID

            try:
                tid, rid = str(UUID(body.table_id)), str(UUID(body.row_id))
            except ValueError:
                raise APIError(422, "INVALID_ID", "UUID identifiers required") from None
            result = await forward(request, "GET", f"/api/v1/tables/{tid}/rows/{rid}")
            if body.fields and result.status_code == 200:
                data = json.loads(result.body)
                data["data"]["values"] = {k: v for k, v in data["data"]["values"].items() if k in body.fields}
                from fastapi.responses import JSONResponse

                return JSONResponse(data)
            return result

        @app.post("/api/v1/tools/xelibrary.search")
        async def search(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
            return await forward(request, "POST", "/api/v1/search/semantic", body.model_dump())

    elif service == "dev-agent":
        from .triage import install as install_triage
        from .work import install

        install(app)
        install_triage(app)
        database = app.state.settings.data_dir / "work.sqlite"
        with transaction(database) as db:
            db.execute("CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, digest TEXT, payload TEXT)")
            previous = app.state.settings.data_dir / "events.sqlite"
            if previous.exists():
                with connect(previous) as old_db:
                    db.executemany(
                        "INSERT OR IGNORE INTO events VALUES(?,?,?)",
                        [tuple(row) for row in old_db.execute("SELECT id,digest,payload FROM events")],
                    )

        @app.post("/api/v1/webhooks/forgejo")
        async def webhook(request: Request):
            secret = (
                Path(os.environ["FORGEJO_WEBHOOK_SECRET_FILE"]).read_text().strip()
                if os.getenv("FORGEJO_WEBHOOK_SECRET_FILE")
                else os.getenv("FORGEJO_WEBHOOK_SECRET", "")
            )
            if not secret:
                raise APIError(503, "WEBHOOK_NOT_CONFIGURED", "webhook secret required")
            raw = await request.body()
            if len(raw) > 1024 * 1024:
                raise APIError(413, "EVENT_TOO_LARGE", "webhook exceeds 1 MiB")
            signature = request.headers.get("X-Forgejo-Signature", "")
            expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise APIError(401, "INVALID_SIGNATURE", "invalid webhook signature")
            event_id = request.headers.get("X-Forgejo-Delivery")
            if not event_id:
                raise APIError(422, "DELIVERY_ID_REQUIRED", "delivery ID required")
            try:
                payload = json.loads(raw)
            except ValueError:
                raise APIError(422, "INVALID_JSON", "invalid webhook JSON") from None
            digest = hashlib.sha256(raw).hexdigest()
            with transaction(database) as db:
                old = db.execute("SELECT digest FROM events WHERE id=?", (event_id,)).fetchone()
                if old and old[0] != digest:
                    raise APIError(409, "EVENT_CONFLICT", "delivery ID reused with different body")
                if not old:
                    if request.headers.get("X-Forgejo-Event") == "issues":
                        app.state.ingest_forgejo(db, payload)
                    db.execute("INSERT INTO events VALUES(?,?,?)", (event_id, digest, json.dumps(payload)))
            return response(request, {"event_id": event_id, "duplicate": bool(old), "status": "received"})

    elif service == "llm-gateway":

        def provider_key():
            if os.getenv("LLM_API_KEY_FILE"):
                return Path(os.environ["LLM_API_KEY_FILE"]).read_text().strip()
            return os.getenv("LLM_API_KEY")

        app.state.ready_check = lambda: bool(provider_key())

        @app.get("/v1/models")
        def models(actor=Depends(scope("llm:use"))):
            return {"object": "list", "data": [{"id": "deepseek-flash", "object": "model"}]}

        @app.post(
            "/v1/chat/completions",
            responses={200: {"content": {"text/event-stream": {}}}},
        )
        async def chat(request: Request, actor=Depends(scope("llm:use"))):
            key = provider_key()
            if not key:
                raise APIError(503, "PROVIDER_NOT_CONFIGURED", "LLM provider credential required")
            body = await request.json()
            if not isinstance(body, dict) or body.get("model") != "deepseek-flash":
                raise APIError(422, "UNSUPPORTED_REQUEST", "only deepseek-flash configured")
            stream_requested = body.get("stream") is True
            if body.get("stream") and not stream_requested:
                raise APIError(422, "UNSUPPORTED_REQUEST", "stream must be a boolean")
            body = {**body, "model": os.getenv("LLM_PROVIDER_MODEL", "deepseek-flash")}
            context = ssl.create_default_context()
            if os.getenv("LLM_TLS_MAX_VERSION") == "1.2":
                context.maximum_version = ssl.TLSVersion.TLSv1_2

            if stream_requested:
                timeout = httpx.Timeout(connect=10, read=120, write=30, pool=10)
                client = httpx.AsyncClient(timeout=timeout, trust_env=False, verify=context)
                upstream = None
                try:
                    upstream_request = client.build_request(
                        "POST",
                        os.getenv("LLM_BASE_URL", "https://api.jyxxe.com").rstrip("/")
                        + "/v1/chat/completions",
                        headers={
                            "Authorization": "Bearer " + key,
                            "Accept": "text/event-stream",
                            "Accept-Encoding": "identity",
                        },
                        json=body,
                    )
                    upstream = await client.send(upstream_request, stream=True)
                except httpx.HTTPError:
                    await client.aclose()
                    raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed") from None

                content_type = upstream.headers.get("content-type", "").lower()
                if upstream.status_code != 200 or not content_type.startswith("text/event-stream"):
                    await upstream.aclose()
                    await client.aclose()
                    raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed")

                async def stream_chunks():
                    def record_stream_error(code):
                        logging.getLogger("xeonolab").warning(
                            json.dumps(
                                {
                                    "service": "llm-gateway",
                                    "event": "provider_stream.interrupted",
                                    "code": code,
                                    "request_id": request.state.request_id,
                                },
                                separators=(",", ":"),
                            )
                        )

                    try:
                        async with asyncio.timeout(600):
                            async for chunk in upstream.aiter_bytes():
                                if chunk:
                                    yield chunk
                    except httpx.HTTPError:
                        # A truncated SSE stream has no successful terminal marker; do not
                        # leak upstream exception details or credentials into a new event.
                        record_stream_error("upstream_read_failed")
                        return
                    except TimeoutError:
                        record_stream_error("stream_timeout")
                        return
                    finally:
                        try:
                            await upstream.aclose()
                        finally:
                            await client.aclose()

                from fastapi.responses import StreamingResponse

                return StreamingResponse(
                    stream_chunks(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )

            async with httpx.AsyncClient(timeout=120, trust_env=False, verify=context) as client:
                result = await client.post(
                    os.getenv("LLM_BASE_URL", "https://api.jyxxe.com").rstrip("/") + "/v1/chat/completions",
                    headers={"Authorization": "Bearer " + key},
                    json=body,
                )
            if result.status_code != 200:
                raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed")
            from fastapi.responses import JSONResponse

            return JSONResponse(result.json())

    if service in ("library-agent", "network-control"):
        app.state.ready_check = lambda: False

    if service == "hf-cache":

        def cache_usage():
            total = 0
            for path in app.state.settings.data_dir.rglob("*"):
                try:
                    if path.is_file():
                        total += path.stat().st_size
                except OSError:
                    continue
            return [({"service": "hf-cache"}, total)]

        app.state.metrics.gauge_provider(
            "hfcache_usage_bytes", "Bytes stored in the local HF cache directory.", cache_usage
        )

    return app
