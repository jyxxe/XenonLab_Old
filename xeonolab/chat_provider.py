"""Trusted chat-provider contracts; no manifest-driven imports or code execution.

The gateway owns authorization, byte/deadline limits and SSE cleanup. A prepared
call snapshots provider configuration for a single request, never the caller's
bearer token. Replacement implementations must return canonical OpenAI JSON/SSE.
"""

import os
import ssl
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from .capabilities import CapabilityIncompatible, load_registry, satisfies
from .common import APIError


class PreparedChat(Protocol):
    def stream_client(self) -> httpx.AsyncClient: ...
    def stream_request(self, client: httpx.AsyncClient) -> httpx.Request: ...
    async def complete(self) -> Any: ...


class ChatProvider(Protocol):
    def configured(self) -> bool: ...
    def models(self) -> list[dict]: ...
    def prepare(self, body: dict) -> PreparedChat: ...


@dataclass(frozen=True, repr=False)
class HTTPChatCall:
    url: str
    body: dict
    headers: dict
    context: ssl.SSLContext
    client_factory: Callable

    def stream_client(self):
        timeout = httpx.Timeout(connect=10, read=120, write=30, pool=10)
        return self.client_factory(timeout=timeout, trust_env=False, verify=self.context)

    def stream_request(self, client):
        return client.build_request("POST", self.url, headers={
            **self.headers, "Accept": "text/event-stream", "Accept-Encoding": "identity",
        }, json=self.body)

    async def complete(self):
        async with self.client_factory(timeout=120, trust_env=False, verify=self.context) as client:
            result = await client.post(self.url, headers=self.headers, json=self.body)
        if result.status_code != 200:
            raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed")
        return result.json()


class HTTPChatProvider:
    """Existing environment-configured OpenAI-compatible provider implementation."""

    def __init__(self, *, client_factory=None):
        self._client_factory = client_factory or (lambda **kwargs: httpx.AsyncClient(**kwargs))

    @staticmethod
    def _key():
        path = os.getenv("LLM_API_KEY_FILE")
        return Path(path).read_text().strip() if path else os.getenv("LLM_API_KEY")

    def configured(self):
        return bool(self._key())

    def models(self):
        return [{"id": "deepseek-flash", "object": "model"}]

    def prepare(self, body):
        if body.get("model") != "deepseek-flash":
            raise APIError(422, "UNSUPPORTED_REQUEST", "only deepseek-flash configured")
        key = self._key()
        if not key:
            raise APIError(503, "PROVIDER_NOT_CONFIGURED", "LLM provider credential required")
        context = ssl.create_default_context()
        if os.getenv("LLM_TLS_MAX_VERSION") == "1.2":
            context.maximum_version = ssl.TLSVersion.TLSv1_2
        return HTTPChatCall(
            os.getenv("LLM_BASE_URL", "https://api.jyxxe.com").rstrip("/") + "/v1/chat/completions",
            {**body, "model": os.getenv("LLM_PROVIDER_MODEL", "deepseek-flash")},
            {"Authorization": "Bearer " + key}, context, self._client_factory,
        )


def prepare_chat(provider, body):
    """Keep provider exception details out of public policy/configuration errors."""
    try:
        return provider.prepare(body)
    except APIError as error:
        messages = {
            (422, "UNSUPPORTED_REQUEST"): "unsupported chat model or request",
            (503, "PROVIDER_NOT_CONFIGURED"): "LLM provider credential required",
        }
        key = (error.status, error.code)
        if key in messages:
            raise APIError(error.status, error.code, messages[key]) from None
        raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed") from None
    except TimeoutError:
        raise APIError(504, "PROVIDER_TIMEOUT", "LLM provider request timed out") from None
    except Exception:
        raise APIError(502, "PROVIDER_ERROR", "LLM provider request failed") from None


@dataclass(frozen=True)
class ResolvedChatProvider:
    provider: ChatProvider = field(repr=False)
    timeout_seconds: int | None = None


CHAT_CAPABILITY = "builtin.platform.provider.llm-chat"


class ChatProviderRegistry:
    """One trusted active binding; replacement/enablement are kernel-only hooks."""

    def __init__(self, provider, *, capability_id=CHAT_CAPABILITY, require="^1.0.0",
                 registry_loader=load_registry, lifecycle_required=None):
        self._provider = provider
        self._enabled = True
        self._capability_id = capability_id
        self._require = require
        self._registry_loader = registry_loader
        mode = os.getenv("CAPABILITY_LIFECYCLE_ENABLED", "0")
        if mode not in {"0", "1"}:
            raise ValueError("CAPABILITY_LIFECYCLE_ENABLED must be 0 or 1")
        self._lifecycle_required = mode == "1" if lifecycle_required is None else lifecycle_required

    def replace(self, provider):
        self._provider = provider

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)

    def require_lifecycle(self, actor):
        check = getattr(actor, "require_capability", None)
        revision = getattr(actor, "lifecycle_revision", None)
        if self._lifecycle_required and (not callable(check) or type(revision) is not int or revision < 0):
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "verified lifecycle snapshot required")
        if callable(check):
            check(self._capability_id)

    def _resolve(self, actor=None):
        if actor is not None:
            self.require_lifecycle(actor)
        if not self._enabled:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such chat provider capability")
        capability = self._registry_loader().lookup(self._capability_id)
        if capability is None:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such chat provider capability")
        permissions = set(capability["permissions"])
        if capability["kind"] != "provider" or capability["runtime"] != "api" or "llm:use" not in permissions:
            raise APIError(503, "CAPABILITY_INVALID", "chat provider does not match its trusted binding")
        if actor is not None:
            for permission in sorted(permissions):
                actor.require(permission)
        if not satisfies(capability["version"], self._require):
            raise CapabilityIncompatible(self._capability_id, capability["version"], self._require)
        if capability["dependencies"]:
            raise APIError(503, "CAPABILITY_DEPENDENCY_UNAVAILABLE", "composite chat providers are not registered")
        budget = capability.get("timeout_seconds")
        if budget is not None and (type(budget) is not int or not 1 <= budget <= 300):
            raise APIError(503, "CAPABILITY_INVALID", "chat provider deadline is invalid")
        return ResolvedChatProvider(self._provider, budget)

    def resolve(self, actor):
        return self._resolve(actor)

    def ready(self):
        try:
            return bool(self._resolve().provider.configured())
        except Exception:
            return False
