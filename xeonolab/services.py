import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
from pathlib import Path

import anyio
import httpx
from fastapi import Depends, Request
from pydantic import BaseModel, Field

from .common import APIError, base_app, connect, response, scope, transaction


def _next_sse_frame(buffer):
    candidates = [(buffer.find(delimiter), len(delimiter)) for delimiter in (b"\r\n\r\n", b"\n\n", b"\r\r")]
    candidates = [(index, length) for index, length in candidates if index >= 0]
    if not candidates:
        return None
    index, length = min(candidates)
    end = index + length
    frame = bytes(buffer[:end])
    del buffer[:end]
    return frame


def _sse_frame_kind(frame):
    event_name = b""
    data_lines = []
    for line in re.split(rb"\r\n|\r|\n", frame):
        if line.startswith(b"event:"):
            event_name = line[6:].strip()
        elif line.startswith(b"data:"):
            value = line[5:]
            data_lines.append(value[1:] if value.startswith(b" ") else value)
    if event_name == b"error":
        return "error"
    data = b"\n".join(data_lines).strip()
    if not data:
        return "keepalive"
    if data == b"[DONE]":
        return "done"
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        return "malformed"
    if isinstance(payload, dict) and "error" in payload:
        return "error"
    if not isinstance(payload, dict) or not isinstance(payload.get("choices"), list):
        return "malformed"
    for choice in payload["choices"]:
        if not isinstance(choice, dict) or not isinstance(choice.get("index"), int):
            return "malformed"
        if not isinstance(choice.get("delta"), dict):
            return "malformed"
        if "finish_reason" not in choice or (
            choice["finish_reason"] is not None and not isinstance(choice["finish_reason"], str)
        ):
            return "malformed"
        delta = choice["delta"]
        if "role" in delta and not isinstance(delta["role"], str):
            return "malformed"
        if "content" in delta and not isinstance(delta["content"], (str, list, type(None))):
            return "malformed"
        tool_calls = delta.get("tool_calls", [])
        if not isinstance(tool_calls, list):
            return "malformed"
        for tool_call in tool_calls:
            if not isinstance(tool_call, dict) or not isinstance(tool_call.get("index"), int):
                return "malformed"
            function = tool_call.get("function", {})
            if not isinstance(function, dict):
                return "malformed"
            if any(key in function and not isinstance(function[key], str) for key in ("name", "arguments")):
                return "malformed"
    return "data"


_SAFE_STREAM_ERROR = (
    b'data: {"error":{"message":"LLM provider stream failed",'
    b'"type":"provider_error","code":"PROVIDER_ERROR"}}\n\n'
)

SERVICES = [
    "xelibrary",
    "embedding",
    "hf-cache",
    "library-agent",
    "lean-verifier",
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
    if service == "lean-verifier":
        from .lean_verifier import create_app as factory

        return factory(settings)

    if service == "tool-gateway":
        from .capability_lifecycle import QUERY_CAPABILITY

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

        async def query_handler(request, body):
            return await forward(request, "POST", "/api/v1/query", body.model_dump())

        async def search_handler(request, body):
            return await forward(request, "POST", "/api/v1/search/semantic", body.model_dump())

        async def get_handler(request, body):
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

        from .tool_adapters import ToolAdapter, ToolAdapters

        definitions = [
            ("xelibrary.search", "Search authorized knowledge", Search, "xelibrary:query", search_handler),
            ("xelibrary.query", "Query authorized knowledge with DSL", ToolQuery, "xelibrary:query", query_handler),
            ("xelibrary.get", "Read an authorized row", ToolGet, "xelibrary:read", get_handler),
        ]
        app.state.tool_adapters = ToolAdapters([
            ToolAdapter(name, "builtin.platform.tool." + name.replace(".", "-"), description, model,
                        frozenset([permission]), handler)
            for name, description, model, permission, handler in definitions
        ])
        adapter_flag = os.getenv("TOOL_CAPABILITY_ADAPTERS", "1")
        if adapter_flag not in {"0", "1"}:
            raise ValueError("TOOL_CAPABILITY_ADAPTERS must be 0 or 1")
        app.state.tool_adapters_enabled = adapter_flag == "1"

        async def invoke(name, actor, request, body, legacy_handler):
            app.state.tool_adapters.require_lifecycle(name, actor)
            if app.state.tool_adapters_enabled:
                return await app.state.tool_adapters.invoke(name, actor, request, body)
            return await legacy_handler(request, body)

        @app.get("/api/v1/tools")
        def tools(request: Request, actor=Depends(scope("xelibrary:query"))):
            if app.state.tool_adapters_enabled:
                return response(request, app.state.tool_adapters.describe(actor))
            return response(request, [
                {"type": "function", "function": {
                    "name": name, "description": description, "parameters": model.model_json_schema(),
                }}
                for name, description, model, _, _ in definitions
                if app.state.tool_adapters.lifecycle_visible(name, actor)
            ])

        @app.post("/api/v1/tools/xelibrary.query")
        async def query(body: ToolQuery, request: Request, actor=Depends(scope("xelibrary:query"))):
            return await invoke("xelibrary.query", actor, request, body, query_handler)

        @app.post("/api/v1/tools/xelibrary.get")
        async def get(body: ToolGet, request: Request, actor=Depends(scope("xelibrary:read"))):
            return await invoke("xelibrary.get", actor, request, body, get_handler)

        @app.post("/api/v1/tools/xelibrary.search")
        async def search(body: Search, request: Request, actor=Depends(scope("xelibrary:query"))):
            return await invoke("xelibrary.search", actor, request, body, search_handler)

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

        def bounded_setting(name, default, minimum, maximum):
            raw = os.getenv(name, str(default))
            try:
                value = int(raw)
            except ValueError:
                raise ValueError(f"{name} must be an integer") from None
            if not minimum <= value <= maximum:
                raise ValueError(f"{name} must be between {minimum} and {maximum}")
            return value

        app.state.llm_stream_timeout_seconds = bounded_setting("LLM_STREAM_TIMEOUT_SECONDS", 600, 1, 3600)
        app.state.llm_stream_max_response_bytes = bounded_setting(
            "LLM_STREAM_MAX_RESPONSE_BYTES", 8 * 1024 * 1024, 1, 64 * 1024 * 1024
        )
        app.state.llm_request_max_bytes = bounded_setting("LLM_REQUEST_MAX_BYTES", 1024 * 1024, 1, 8 * 1024 * 1024)
        stream_capacity = bounded_setting("LLM_STREAM_MAX_ACTIVE", 8, 1, 64)
        app.state.llm_stream_slots = asyncio.Queue(maxsize=stream_capacity)
        for _ in range(stream_capacity):
            app.state.llm_stream_slots.put_nowait(object())

        from .chat_provider import ChatProviderRegistry, HTTPChatProvider, ResolvedChatProvider, prepare_chat

        default_provider = HTTPChatProvider()
        app.state.chat_providers = ChatProviderRegistry(default_provider)
        provider_flag = os.getenv("LLM_CAPABILITY_ADAPTERS", "1")
        if provider_flag not in {"0", "1"}:
            raise ValueError("LLM_CAPABILITY_ADAPTERS must be 0 or 1")
        app.state.llm_capability_adapters = provider_flag == "1"

        def resolved_provider(actor):
            app.state.chat_providers.require_lifecycle(actor)
            if app.state.llm_capability_adapters:
                return app.state.chat_providers.resolve(actor)
            return ResolvedChatProvider(default_provider)

        app.state.ready_check = lambda: (
            app.state.chat_providers.ready() if app.state.llm_capability_adapters else default_provider.configured()
        )

        @app.get("/v1/models")
        def models(actor=Depends(scope("llm:use"))):
            provider = resolved_provider(actor).provider
            try:
                from fastapi.responses import JSONResponse

                return JSONResponse({"object": "list", "data": provider.models()}, headers={"Cache-Control": "no-store"})
            except Exception:
                raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed") from None

        @app.post(
            "/v1/chat/completions",
            responses={
                200: {
                    "description": "JSON completion, or SSE stream. Midstream failures are safe error data events without data: [DONE].",
                    "content": {"application/json": {}, "text/event-stream": {}},
                },
                408: {"description": "The inbound request body exceeded its read deadline."},
                413: {"description": "Request body exceeds the configured byte limit."},
                429: {"description": "Per-process stream capacity reached; includes Retry-After."},
                502: {"description": "Provider request failed before streaming began, or JSON completion failed."},
                504: {"description": "Provider request exceeded its deadline before streaming began or during JSON completion."},
            },
        )
        async def chat(request: Request, actor=Depends(scope("llm:use"))):
            app.state.chat_providers.require_lifecycle(actor)
            started = asyncio.get_running_loop().time()
            deadline = started + app.state.llm_stream_timeout_seconds
            try:
                slot = app.state.llm_stream_slots.get_nowait()
            except asyncio.QueueEmpty:
                raise APIError(
                    429,
                    "LLM_STREAM_CAPACITY",
                    "chat capacity reached; retry later",
                    headers={"Retry-After": "5"},
                ) from None

            slot_released = False

            def release_slot():
                nonlocal slot_released
                if not slot_released:
                    app.state.llm_stream_slots.put_nowait(slot)
                    slot_released = True

            try:
                async with asyncio.timeout_at(deadline):
                    content_length = request.headers.get("content-length")
                    if content_length is not None:
                        try:
                            declared_length = int(content_length)
                        except ValueError:
                            raise APIError(400, "INVALID_CONTENT_LENGTH", "invalid content length") from None
                        if declared_length < 0:
                            raise APIError(400, "INVALID_CONTENT_LENGTH", "invalid content length")
                        if declared_length > app.state.llm_request_max_bytes:
                            raise APIError(413, "REQUEST_TOO_LARGE", "chat request exceeds the byte limit")
                    raw_body = bytearray()
                    async for chunk in request.stream():
                        if len(raw_body) + len(chunk) > app.state.llm_request_max_bytes:
                            raise APIError(413, "REQUEST_TOO_LARGE", "chat request exceeds the byte limit")
                        raw_body.extend(chunk)
                    try:
                        body = json.loads(raw_body)
                    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
                        raise APIError(422, "INVALID_REQUEST", "request body must be valid JSON") from None
                    if not isinstance(body, dict):
                        raise APIError(422, "UNSUPPORTED_REQUEST", "chat request must be an object")
                    stream_requested = body.get("stream") is True
                    if body.get("stream") and not stream_requested:
                        raise APIError(422, "UNSUPPORTED_REQUEST", "stream must be a boolean")
            except TimeoutError:
                release_slot()
                raise APIError(408, "REQUEST_TIMEOUT", "chat request body timed out") from None
            except BaseException:
                release_slot()
                raise

            try:
                resolved = resolved_provider(actor)
                call = prepare_chat(resolved.provider, body)
                if resolved.timeout_seconds is not None:
                    deadline = min(deadline, started + resolved.timeout_seconds)
            except BaseException:
                release_slot()
                raise

            if stream_requested:
                client = None
                upstream = None
                send_task = None
                disconnect_task = None
                handed_off = False
                cleanup_task = None

                async def wait_for_disconnect():
                    while True:
                        message = await request.receive()
                        if message["type"] == "http.disconnect":
                            return

                async def perform_cleanup():
                    try:
                        if upstream is not None:
                            try:
                                await upstream.aclose()
                            except Exception:
                                logging.getLogger("xeonolab").warning(
                                    json.dumps(
                                        {
                                            "service": "llm-gateway",
                                            "event": "provider_stream.close_failed",
                                            "request_id": getattr(request.state, "request_id", ""),
                                        },
                                        separators=(",", ":"),
                                    )
                                )
                    finally:
                        try:
                            if client is not None:
                                try:
                                    await client.aclose()
                                except Exception:
                                    logging.getLogger("xeonolab").warning(
                                        json.dumps(
                                            {
                                                "service": "llm-gateway",
                                                "event": "provider_client.close_failed",
                                                "request_id": getattr(request.state, "request_id", ""),
                                            },
                                            separators=(",", ":"),
                                        )
                                    )
                        finally:
                            release_slot()

                async def close_resources():
                    nonlocal cleanup_task
                    if cleanup_task is None:
                        cleanup_task = asyncio.create_task(perform_cleanup())
                    try:
                        with anyio.CancelScope(shield=True):
                            await asyncio.shield(cleanup_task)
                    except asyncio.CancelledError:
                        # Raw asyncio cancellation bypasses AnyIO's cancel scope. Keep
                        # waiting in a fresh shield until provider/client cleanup ends.
                        with anyio.CancelScope(shield=True):
                            await cleanup_task
                        raise

                try:
                    client = call.stream_client()
                    async with asyncio.timeout_at(deadline):
                        upstream_request = call.stream_request(client)
                        send_task = asyncio.create_task(client.send(upstream_request, stream=True))
                        disconnect_task = asyncio.create_task(wait_for_disconnect())
                        done, _ = await asyncio.wait(
                            (send_task, disconnect_task), return_when=asyncio.FIRST_COMPLETED
                        )
                        if disconnect_task in done:
                            if send_task.done() and not send_task.cancelled():
                                upstream = send_task.result()
                            else:
                                send_task.cancel()
                                await asyncio.gather(send_task, return_exceptions=True)
                            raise asyncio.CancelledError
                        upstream = await send_task

                    disconnect_task.cancel()
                    await asyncio.gather(disconnect_task, return_exceptions=True)
                    content_type = upstream.headers.get("content-type", "").lower()
                    if upstream.status_code != 200 or not content_type.startswith("text/event-stream"):
                        raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed")
                    handed_off = True

                except asyncio.CancelledError:
                    raise
                except TimeoutError:
                    raise APIError(504, "PROVIDER_TIMEOUT", "LLM provider request timed out") from None
                except httpx.HTTPError:
                    raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed") from None
                except Exception:
                    raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed") from None
                finally:
                    if disconnect_task is not None:
                        disconnect_task.cancel()
                        await asyncio.gather(disconnect_task, return_exceptions=True)
                    if send_task is not None and not send_task.done():
                        send_task.cancel()
                        await asyncio.gather(send_task, return_exceptions=True)
                    if not handed_off:
                        await close_resources()

                async def stream_chunks():
                    def record_stream_error(code):
                        logging.getLogger("xeonolab").warning(
                            json.dumps(
                                {
                                    "service": "llm-gateway",
                                    "event": "provider_stream.interrupted",
                                    "code": code,
                                    "request_id": getattr(request.state, "request_id", ""),
                                },
                                separators=(",", ":"),
                            )
                        )

                    total_bytes = 0
                    buffer = bytearray()
                    try:
                        async with asyncio.timeout_at(deadline):
                            async for chunk in upstream.aiter_bytes():
                                total_bytes += len(chunk)
                                if total_bytes > app.state.llm_stream_max_response_bytes:
                                    record_stream_error("response_limit")
                                    yield _SAFE_STREAM_ERROR
                                    return
                                buffer.extend(chunk)
                                while (frame := _next_sse_frame(buffer)) is not None:
                                    kind = _sse_frame_kind(frame)
                                    if kind == "error":
                                        record_stream_error("provider_error_event")
                                        yield _SAFE_STREAM_ERROR
                                        return
                                    if kind == "malformed":
                                        record_stream_error("malformed_chat_completion_chunk")
                                        yield _SAFE_STREAM_ERROR
                                        return
                                    yield frame
                                    if kind == "done":
                                        return
                        if buffer:
                            record_stream_error("incomplete_sse_frame")
                            yield _SAFE_STREAM_ERROR
                        else:
                            record_stream_error("missing_done_marker")
                            yield _SAFE_STREAM_ERROR
                    except httpx.HTTPError:
                        record_stream_error("upstream_read_failed")
                        yield _SAFE_STREAM_ERROR
                        return
                    except TimeoutError:
                        record_stream_error("stream_timeout")
                        yield _SAFE_STREAM_ERROR
                        return
                    except Exception:
                        record_stream_error("provider_adapter_failed")
                        yield _SAFE_STREAM_ERROR
                        return
                    finally:
                        await close_resources()

                from fastapi.responses import StreamingResponse

                class CleanupStreamingResponse(StreamingResponse):
                    async def __call__(self, scope, receive, send):
                        try:
                            await super().__call__(scope, receive, send)
                        finally:
                            await close_resources()

                response_stream = CleanupStreamingResponse(
                    stream_chunks(),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
                )
                handed_off = True
                return response_stream

            release_slot()
            from fastapi.responses import JSONResponse

            try:
                async with asyncio.timeout_at(deadline):
                    return JSONResponse(await call.complete())
            except (TimeoutError, httpx.TimeoutException):
                raise APIError(504, "PROVIDER_TIMEOUT", "LLM provider request timed out") from None
            except Exception:
                raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed") from None

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
