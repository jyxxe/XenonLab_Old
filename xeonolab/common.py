import hashlib
import json
import logging
import math
import os
import sqlite3
import ssl
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit
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
    def __init__(self, status, code, message, headers=None, details=None):
        self.status, self.code, self.message = status, code, message
        self.headers = headers or {}
        self.details = details or {}


DEFAULT_RATE_LIMITS = {
    "interactive": {"requests": 120, "window_seconds": 60},
    "bulk": {"requests": 10, "window_seconds": 60},
    "admin": {"requests": 60, "window_seconds": 60},
    "agent": {"requests": 60, "window_seconds": 60},
    "embedding-fast": {"requests": 60, "window_seconds": 60},
    "embedding-quality": {"requests": 6, "window_seconds": 60},
}


@dataclass
class Identity:
    sub: str
    scopes: set[str]
    kind: str = "human"
    issued_at: float = 0
    credential_epoch: str = ""
    ancestors: tuple[str, ...] = ()
    token_scopes: set[str] | None = None

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
    rate_limit_enabled: bool = True
    rate_limit_policies: dict = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_RATE_LIMITS)))
    oidc_issuers_by_origin: dict[str, str] = field(default_factory=dict)
    oidc_token_urls: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.oidc_issuers_by_origin, dict) or not isinstance(self.oidc_token_urls, dict):
            raise ValueError("OIDC issuer and token URL settings must be JSON objects")
        issuers = {self.issuer, *self.oidc_issuers_by_origin.values()} - {""}
        for origin, issuer in self.oidc_issuers_by_origin.items():
            origin_parts = urlsplit(origin) if isinstance(origin, str) else None
            if (
                not origin_parts
                or origin_parts.scheme != "https"
                or not origin_parts.hostname
                or origin_parts.username
                or origin_parts.password
                or origin_parts.path
                or origin_parts.query
                or origin_parts.fragment
                or not isinstance(issuer, str)
            ):
                raise ValueError("invalid OIDC issuer origin mapping")
        for issuer in issuers:
            parts = urlsplit(issuer) if isinstance(issuer, str) else None
            if (
                not parts
                or parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or (parts.path and not parts.path.startswith("/"))
                or parts.query
                or parts.fragment
            ):
                raise ValueError("OIDC issuers must be exact HTTPS URLs")
        if not set(self.oidc_token_urls) <= issuers:
            raise ValueError("OIDC token URL keys must match configured issuers")
        for issuer, endpoint in self.oidc_token_urls.items():
            issuer_parts = urlsplit(issuer)
            parts = urlsplit(endpoint) if isinstance(endpoint, str) else None
            if (
                not parts
                or parts.scheme != "https"
                or not parts.hostname
                or parts.username
                or parts.password
                or parts.query
                or parts.fragment
                or parts.hostname != issuer_parts.hostname
                or parts.port != issuer_parts.port
                or parts.path != issuer_parts.path.rstrip("/") + "/protocol/openid-connect/token"
            ):
                raise ValueError("OIDC token endpoints must match their exact HTTPS issuer")

    @property
    def allowed_issuers(self):
        """Exact issuer values accepted by API JWT verifiers."""
        return frozenset({self.issuer, *self.oidc_issuers_by_origin.values()} - {""})

    @classmethod
    def environment(cls, service):
        policies = json.loads(json.dumps(DEFAULT_RATE_LIMITS))
        overrides = json.loads(os.getenv("RATE_LIMIT_POLICIES_JSON", "{}"))
        if not isinstance(overrides, dict):
            raise ValueError("RATE_LIMIT_POLICIES_JSON must be a JSON object")
        for group, override in overrides.items():
            if group not in policies or not isinstance(override, dict):
                raise ValueError(f"invalid rate-limit policy group: {group}")
            policies[group].update(override)
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
            os.getenv("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
            policies,
            json.loads(os.getenv("OIDC_ISSUERS_BY_ORIGIN_JSON", "{}")),
            json.loads(os.getenv("OIDC_TOKEN_URLS_JSON", "{}")),
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
                issuer=list(settings.allowed_issuers) if settings.allowed_issuers else settings.issuer,
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
        tuple(claims.get("ancestors", ())),
        token_scopes=set(scopes),
    )


def identity(request: Request):
    settings = request.app.state.settings
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        raise APIError(401, "UNAUTHENTICATED", "bearer token required")
    token = header[7:]
    resolver = getattr(request.app.state, "authorization_resolver", None)
    if resolver:
        actor = resolver(token)
        enforce_rate_limit(request, actor)
        return actor
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
            if result.status_code == 429:
                retry_after = result.headers.get("Retry-After")
                headers = {"Retry-After": retry_after} if retry_after else {}
                raise APIError(429, "AUTHORIZATION_RATE_LIMITED", "authorization authority rate limited the request", headers)
            result.raise_for_status()
            data = result.json()["data"]
            if local and (data["subject"] != local.sub or data["kind"] != local.kind):
                raise ValueError("identity authority mismatch")
            actor = Identity(
                data["subject"], set(data["scopes"]), data["kind"], ancestors=tuple(data.get("ancestors", []))
            )
            enforce_rate_limit(request, actor)
            return actor
        except (httpx.HTTPError, ValueError, KeyError, OSError):
            raise APIError(503, "AUTHORIZATION_UNAVAILABLE", "account authority unavailable") from None
    if token.startswith("xek_"):
        raise APIError(503, "AUTHORIZATION_NOT_CONFIGURED", "account authority required for API keys")
    actor = token_identity(token, settings)
    enforce_rate_limit(request, actor)
    return actor


def route_group(request, actor):
    path = request.url.path.lower()
    body = {}
    raw_body = getattr(request, "_body", None)
    if raw_body:
        try:
            body = json.loads(raw_body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            pass
    if path.endswith(("/search/semantic", "/search/hybrid")):
        return "embedding-quality" if body.get("mode") == "quality" else "embedding-fast"
    if path in {"/api/v1/embed/image", "/api/v1/search/image"}:
        return "embedding-quality"
    if path.endswith("/embed/text") or path.endswith("/v1/embeddings"):
        if body.get("model") in {"quality", "Qwen/Qwen3-VL-Embedding-2B"}:
            return "embedding-quality"
        return "embedding-fast"
    if "/embedding/quality" in path:
        return "embedding-quality"
    if "/embedding/" in path or "/embed/" in path or "/embeddings/" in path or path.endswith("/embeddings"):
        return "embedding-fast"
    if any(segment in path for segment in ("/bulk", "/import", "/export", "/index")):
        return "bulk"
    if any(
        path.startswith(prefix)
        for prefix in (
            "/api/v1/auth/accounts",
            "/api/v1/deploy",
            "/api/v1/deployments",
            "/api/v1/releases",
            "/api/v1/acl",
            "/api/v1/audit",
            "/api/v1/apps",
        )
    ) or path.endswith("/schema"):
        return "admin"
    return "agent" if actor.kind in {"agent", "codex", "opencode", "devagent"} else "interactive"


def enforce_rate_limit(request, actor):
    settings = request.app.state.settings
    versioned_api = request.url.path.startswith(("/api/v1/", "/v1/"))
    if (
        not settings.rate_limit_enabled
        or not versioned_api
        or request.url.path == "/api/v1/auth/authorization"
    ):
        return
    group = route_group(request, actor)
    policy = settings.rate_limit_policies.get(group)
    if policy is None:
        return
    try:
        limit = int(policy["requests"])
        window = float(policy["window_seconds"])
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(f"invalid rate-limit policy for group {group}") from None
    if limit <= 0 or not math.isfinite(window) or window <= 0:
        raise RuntimeError(f"invalid rate-limit policy for group {group}")

    now = time.time()
    cutoff = now - window
    identity_key = hashlib.sha256(actor.sub.encode("utf-8")).hexdigest()
    store = request.app.state.rate_limit_db
    with transaction(store) as db:
        db.execute(
            "DELETE FROM rate_limit_requests WHERE route_group=? AND created_at<=?",
            (group, cutoff),
        )
        recent = db.execute(
            "SELECT created_at FROM rate_limit_requests WHERE identity_key=? AND route_group=? "
            "AND created_at>? ORDER BY created_at LIMIT ?",
            (identity_key, group, cutoff, limit),
        ).fetchall()
        if len(recent) >= limit:
            retry_after = max(1, math.ceil(float(recent[0]["created_at"]) + window - now))
        else:
            db.execute(
                "INSERT INTO rate_limit_requests(identity_key,route_group,created_at) VALUES(?,?,?)",
                (identity_key, group, now),
            )
            return

    logging.getLogger("xeonolab.audit").warning(
        json.dumps(
            {
                "event": "rate_limit.rejected",
                "request_id": getattr(request.state, "request_id", ""),
                "identity_hash": identity_key,
                "route_group": group,
                "route": request.url.path,
                "limit": limit,
                "window_seconds": window,
                "retry_after": retry_after,
            },
            separators=(",", ":"),
        )
    )
    raise APIError(429, "RATE_LIMITED", "request rate limit exceeded", {"Retry-After": str(retry_after)})


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
    if app.state.settings.rate_limit_enabled:
        for group in DEFAULT_RATE_LIMITS:
            policy = app.state.settings.rate_limit_policies.get(group)
            try:
                limit, window = int(policy["requests"]), float(policy["window_seconds"])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"invalid rate-limit policy for group {group}") from None
            if limit <= 0 or not math.isfinite(window) or window <= 0:
                raise ValueError(f"invalid rate-limit policy for group {group}")
        if set(app.state.settings.rate_limit_policies) - set(DEFAULT_RATE_LIMITS):
            raise ValueError("unknown rate-limit policy group configured")
    app.state.settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.requests = 0
    app.state.errors = 0
    app.state.rate_limit_db = app.state.settings.data_dir / "rate-limits.sqlite3"
    with connect(app.state.rate_limit_db) as db:
        db.execute(
            "CREATE TABLE IF NOT EXISTS rate_limit_requests("
            "identity_key TEXT NOT NULL, route_group TEXT NOT NULL, created_at REAL NOT NULL)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS rate_limit_window_idx "
            "ON rate_limit_requests(identity_key,route_group,created_at)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS rate_limit_expiry_idx ON rate_limit_requests(created_at)"
        )
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

        def record_request(status, duration):
            route = getattr(request.scope.get("route"), "path", None) or "unmatched"
            app.state.requests += 1
            app.state.errors += status >= 400
            metrics = app.state.metrics
            metrics.counter("xeonolab_requests_total", help="HTTP requests handled.", service=service)
            metrics.counter(
                "xeonolab_route_requests_total",
                1,
                help="HTTP requests by route, method and status code.",
                service=service,
                route=route,
                method=request.method,
                status=status,
            )
            metrics.observe(
                "xeonolab_request_duration_seconds",
                duration,
                help="HTTP request duration by route, method and status code.",
                service=service,
                route=route,
                method=request.method,
                status=status,
            )
            if status >= 400:
                metrics.counter("xeonolab_errors_total", help="HTTP error responses.", service=service)
            logging.getLogger("xeonolab").info(
                json.dumps(
                    {
                        "service": service,
                        "request_id": request.state.request_id,
                        "event": "request.completed",
                        "status": status,
                        "duration_ms": round(duration * 1000, 2),
                    }
                )
            )

        started = time.monotonic()
        try:
            result = await call_next(request)
        except Exception:
            duration = time.monotonic() - started
            record_request(500, duration)
            raise

        duration = time.monotonic() - started
        result.headers["X-Request-ID"] = request.state.request_id
        if request.url.path.startswith("/api/v1/auth/"):
            result.headers["Cache-Control"] = "no-store"
        record_request(result.status_code, duration)
        return result

    @app.exception_handler(APIError)
    async def api_error(request, exc):
        return JSONResponse(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                    "request_id": request.state.request_id,
                }
            },
            status_code=exc.status,
            headers=exc.headers,
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
        extra = getattr(app.state, "metrics_extra", None)
        extra_text = ""
        if extra:
            available = 1
            try:
                extra_text = extra()
            except Exception:
                available = 0
                logging.getLogger("xeonolab").warning(
                    json.dumps({"service": service, "event": "metrics.extra_failed"})
                )
            app.state.metrics.gauge(
                "xeonolab_metrics_extra_available",
                available,
                help="Whether the additional service metrics were available on this scrape.",
                service=service,
            )
        return PlainTextResponse(
            app.state.metrics.render() + extra_text,
            media_type="text/plain; version=0.0.4",
        )

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
