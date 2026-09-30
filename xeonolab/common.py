import json
import logging
import os
import sqlite3
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx
import jwt
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException

DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


def escape_labels(labels):
    """Prometheus text format label value escaping."""
    return str(labels).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def format_number(value):
    value = float(value)
    return str(int(value)) if value.is_integer() else repr(value)


class Metrics:
    """Minimal in-process Prometheus text-format registry (no external dependency).

    Counters, gauges and histograms; gauges may be backed by a provider that is
    evaluated at scrape time so state is read from the database instead of being
    cached in memory. Series names, help and types stay stable across scrapes.
    """

    def __init__(self, service=""):
        self.service = service
        self._lock = threading.Lock()
        self._types = {}
        self._help = {}
        self._series = {}
        self._histograms = {}
        self._providers = {}

    def _labels_key(self, labels):
        return tuple(sorted((str(key), str(value)) for key, value in labels.items()))

    def _register(self, name, kind, help_text=None):
        current = self._types.get(name)
        if current is not None and current != kind:
            raise ValueError(f"metric {name} already registered as {current}")
        self._types[name] = kind
        if help_text is not None:
            self._help[name] = help_text

    def counter(self, name, amount=1.0, help=None, **labels):
        with self._lock:
            self._register(name, "counter", help)
            key = (name, self._labels_key(labels))
            self._series[key] = self._series.get(key, 0.0) + float(amount)

    def gauge(self, name, value, help=None, **labels):
        with self._lock:
            self._register(name, "gauge", help)
            self._series[(name, self._labels_key(labels))] = float(value)

    def observe(self, name, value, buckets=DEFAULT_BUCKETS, help=None, **labels):
        with self._lock:
            self._register(name, "histogram", help)
            key = (name, self._labels_key(labels))
            entry = self._histograms.get(key)
            if entry is None:
                entry = {
                    "labels": dict(labels),
                    "buckets": tuple(buckets),
                    "counts": [0] * len(buckets),
                    "sum": 0.0,
                    "count": 0,
                }
                self._histograms[key] = entry
            value = float(value)
            entry["sum"] += value
            entry["count"] += 1
            for index, boundary in enumerate(entry["buckets"]):
                if value <= boundary:
                    entry["counts"][index] += 1

    def gauge_provider(self, name, help, function):
        """Register a gauge family evaluated at scrape time: function() -> [(labels, value)]."""
        with self._lock:
            self._register(name, "gauge", help)
            self._providers[name] = function

    @staticmethod
    def _line(name, labels, value):
        if labels:
            rendered = ",".join(f'{key}="{escape_labels(value)}"' for key, value in sorted(labels.items()))
            return f"{name}{{{rendered}}} {format_number(value)}"
        return f"{name} {format_number(value)}"

    def render(self):
        with self._lock:
            types = dict(self._types)
            help_texts = dict(self._help)
            series = [(name, dict(labels), value) for (name, labels), value in self._series.items()]
            histograms = [
                (name, dict(entry["labels"]), entry["buckets"], list(entry["counts"]), entry["sum"], entry["count"])
                for (name, _), entry in self._histograms.items()
            ]
            providers = dict(self._providers)

        output = []

        def header(name):
            output.append(f"# TYPE {name} {types[name]}")
            if name in help_texts:
                output.append(f"# HELP {name} {help_texts[name]}")

        for name in sorted({name for name, _, _ in series}):
            header(name)
            for series_name, labels, value in sorted(
                series, key=lambda item: (item[0], sorted(item[1].items()))
            ):
                if series_name == name:
                    output.append(self._line(name, labels, value))
        for name, function in sorted(providers.items()):
            header(name)
            try:
                entries = list(function())
            except Exception:  # a broken provider must not break the scrape
                logging.getLogger("xeonolab").warning(
                    json.dumps({"event": "metrics.provider_failed", "metric": name})
                )
                continue
            for labels, value in entries:
                output.append(self._line(name, labels or {}, value))
        for name in sorted({item[0] for item in histograms}):
            header(name)
            for histogram_name, labels, buckets, counts, total, count in sorted(
                histograms, key=lambda item: (item[0], sorted(item[1].items()))
            ):
                if histogram_name != name:
                    continue
                for boundary, observed in zip(buckets, counts):
                    output.append(
                        self._line(name + "_bucket", {**labels, "le": format_number(boundary)}, observed)
                    )
                output.append(self._line(name + "_bucket", {**labels, "le": "+Inf"}, count))
                output.append(self._line(name + "_sum", labels, total))
                output.append(self._line(name + "_count", labels, count))
        return "".join(line + "\n" for line in output)



class APIError(Exception):
    def __init__(self, status, code, message):
        self.status, self.code, self.message = status, code, message


@dataclass
class Identity:
    sub: str
    scopes: set[str]
    kind: str = "human"
    issued_at: float = 0
    credential_epoch: str = ""
    ancestors: tuple[str, ...] = ()

    def require(self, scope):
        if scope not in self.scopes:
            raise APIError(403, "FORBIDDEN", f"scope required: {scope}")


@dataclass
class Settings:
    data_dir: Path
    mode: str = "production"
    issuer: str = ""
    audience: str = "xeonolab"
    public_key: str = ""
    dev_tokens: dict | None = None
    role_scopes: dict | None = None

    @classmethod
    def environment(cls, service):
        return cls(
            Path(os.getenv("DATA_DIR", f"runtime/{service}")),
            os.getenv("APP_MODE", "production"),
            os.getenv("OIDC_ISSUER", ""),
            os.getenv("OIDC_AUDIENCE", "xeonolab"),
            (
                Path(os.environ["OIDC_PUBLIC_KEY_FILE"]).read_text()
                if os.getenv("OIDC_PUBLIC_KEY_FILE")
                else os.getenv("OIDC_PUBLIC_KEY", "")
            ),
            json.loads(os.getenv("DEV_TOKENS_JSON", "{}")),
            json.loads(os.getenv("OIDC_ROLE_SCOPES_JSON", "{}")),
        )


def token_identity(token, settings):
    if settings.mode == "development" and token in (settings.dev_tokens or {}):
        claims = settings.dev_tokens[token]
    else:
        if not settings.public_key or not settings.issuer:
            raise APIError(503, "AUTH_NOT_CONFIGURED", "OIDC verification not configured")
        try:
            claims = jwt.decode(
                token,
                settings.public_key,
                algorithms=["RS256"],
                issuer=settings.issuer,
                audience=settings.audience,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            raise APIError(401, "INVALID_TOKEN", "invalid access token") from None
    scopes = set(claims.get("scope", "").split()) | set(claims.get("platform_scopes", "").split())
    for role in claims.get("realm_access", {}).get("roles", []):
        scopes.update((settings.role_scopes or {}).get(role, []))
    return Identity(
        claims["sub"],
        scopes,
        claims.get("agent_kind", "human"),
        claims.get("iat", 0),
        claims.get("account_epoch", ""),
    )


def identity(request: Request):
    settings = request.app.state.settings
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise APIError(401, "UNAUTHENTICATED", "bearer token required")
    token = header[7:]
    resolver = getattr(request.app.state, "authorization_resolver", None)
    if resolver:
        return resolver(token)
    endpoint = os.getenv("AUTHORIZATION_URL")
    if endpoint:
        # JWTs retain local issuer/signature validation. Opaque keys are verified
        # only by the fixed, TLS-authenticated account authority.
        local = None if token.startswith("xek_") else token_identity(token, settings)
        try:
            context = ssl.create_default_context(cafile=os.getenv("OIDC_CA_FILE"))
            with httpx.Client(verify=context, trust_env=False, timeout=10) as client:
                result = client.get(endpoint, headers={"Authorization": header})
            if result.status_code in (401, 403):
                raise APIError(result.status_code, "AUTHORIZATION_REVOKED", "identity or permission revoked")
            result.raise_for_status()
            data = result.json()["data"]
            if local and (data["subject"] != local.sub or data["kind"] != local.kind):
                raise ValueError("identity authority mismatch")
            return Identity(
                data["subject"], set(data["scopes"]), data["kind"], ancestors=tuple(data.get("ancestors", []))
            )
        except (httpx.HTTPError, ValueError, KeyError, OSError):
            raise APIError(503, "AUTHORIZATION_UNAVAILABLE", "account authority unavailable") from None
    if token.startswith("xek_"):
        raise APIError(503, "AUTHORIZATION_NOT_CONFIGURED", "account authority required for API keys")
    return token_identity(token, settings)


def scope(name):
    def dependency(actor: Identity = Depends(identity)):
        actor.require(name)
        return actor

    return dependency


def response(request, data, **meta):
    return {"data": data, "meta": {"request_id": request.state.request_id, **meta}}


def base_app(service, settings=None):
    app = FastAPI(title=f"XeonoLab {service}", version="0.1.0")
    app.state.settings = settings or Settings.environment(service)
    app.state.settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.requests = 0
    app.state.errors = 0
    app.state.metrics = Metrics(service)
    app.state.metrics.gauge(
        "xeonolab_build_info", 1, help="Build and service identity.", service=service, version="0.1.0"
    )

    @app.middleware("http")
    async def context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        supplied = request.headers.get("X-Request-ID", "")
        try:
            from uuid import UUID

            request.state.request_id = str(UUID(supplied))
        except ValueError:
            pass
        started = time.monotonic()
        result = await call_next(request)
        duration = time.monotonic() - started
        route = getattr(request.scope.get("route"), "path", None) or "unmatched"
        result.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/v1/auth/"):
            result.headers["Cache-Control"] = "no-store"
        app.state.requests += 1
        app.state.errors += result.status_code >= 400
        metrics = app.state.metrics
        metrics.counter("xeonolab_requests_total", help="HTTP requests handled.", service=service)
        metrics.counter(
            "xeonolab_route_requests_total",
            1,
            help="HTTP requests by route, method and status code.",
            service=service,
            route=route,
            method=request.method,
            status=result.status_code,
        )
        metrics.observe(
            "xeonolab_request_duration_seconds",
            duration,
            help="HTTP request duration by route, method and status code.",
            service=service,
            route=route,
            method=request.method,
            status=result.status_code,
        )
        if result.status_code >= 400:
            metrics.counter("xeonolab_errors_total", help="HTTP error responses.", service=service)
        logging.getLogger("xeonolab").info(
            json.dumps(
                {
                    "service": service,
                    "request_id": request.state.request_id,
                    "event": "request.completed",
                    "status": result.status_code,
                    "duration_ms": round(duration * 1000, 2),
                }
            )
        )
        return result

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": {},
                    "request_id": request.state.request_id,
                }
            },
            status_code=exc.status,
        )

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return await api_error(request, APIError(422, "VALIDATION_ERROR", "invalid request fields"))

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return await api_error(request, APIError(exc.status_code, "HTTP_ERROR", str(exc.detail)))

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return await api_error(request, APIError(500, "INTERNAL_ERROR", "internal service error"))

    @app.get("/health/live")
    def live():
        return {"status": "live", "service": service}

    @app.get("/health/ready")
    def ready():
        check = getattr(app.state, "ready_check", lambda: True)
        if not check():
            raise APIError(503, "NOT_READY", "required dependencies are unavailable")
        return {"status": "ready", "service": service}

    @app.get("/version")
    def version():
        return {"service": service, "version": "0.1.0"}

    @app.get("/metrics", response_class=PlainTextResponse)
    def metrics():
        return app.state.metrics.render()

    return app


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(path):
    db = sqlite3.connect(path, timeout=30, factory=ClosingConnection)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    return db


@contextmanager
def transaction(path):
    db = connect(path)
    try:
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def etag(revision):
    return f'"revision:{revision}"'


def precondition(request, revision):
    if request.headers.get("If-Match") != etag(revision):
        raise APIError(412, "STALE_REVISION", "If-Match must equal the current ETag")
