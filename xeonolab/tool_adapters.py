"""Trusted static async tool adapters; manifests never select executable code.

Only platform code constructs these bindings. A UI plugin install cannot add a
handler. Async deadlines bound cooperative I/O, not arbitrary Python CPU work;
independent or untrusted execution still requires the Phase 3 service runtime.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from httpx import TimeoutException
from pydantic import BaseModel

from .capabilities import CapabilityIncompatible, load_registry, satisfies
from .common import APIError


@dataclass(frozen=True)
class ToolAdapter:
    name: str
    capability_id: str
    description: str
    model: type[BaseModel]
    permissions: frozenset[str]
    handler: Callable[[Any, BaseModel], Awaitable[Any]]
    require: str = "^1.0.0"


class ToolAdapters:
    def __init__(self, adapters, *, registry_loader=load_registry):
        self._adapters = {}
        self._disabled = set()
        self._capability_names = {}
        self._registry_loader = registry_loader
        for adapter in adapters:
            if adapter.name in self._adapters or adapter.capability_id in self._capability_names:
                raise ValueError("duplicate tool adapter")
            if not asyncio.iscoroutinefunction(adapter.handler):
                raise ValueError("tool handlers must be trusted async functions")
            self._adapters[adapter.name] = adapter
            self._capability_names[adapter.capability_id] = adapter.name

    def set_enabled(self, name, enabled):
        """Trusted process-local lifecycle hook; deliberately no HTTP endpoint."""
        if name not in self._adapters:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such tool capability")
        if enabled:
            self._disabled.discard(name)
        else:
            self._disabled.add(name)

    def resolve(self, name, actor):
        registry = self._registry_loader()
        checked_results = {}

        def checked(tool_name):
            if tool_name in checked_results:
                return checked_results[tool_name]
            adapter = self._adapters.get(tool_name)
            if adapter is None or tool_name in self._disabled:
                raise APIError(404, "CAPABILITY_NOT_FOUND", "no such tool capability")
            capability = registry.lookup(adapter.capability_id)
            if capability is None:
                raise APIError(404, "CAPABILITY_NOT_FOUND", "no such tool capability")
            permissions = set(capability["permissions"])
            if capability["kind"] != "tool" or capability["runtime"] != "api" or not adapter.permissions <= permissions:
                raise APIError(503, "CAPABILITY_INVALID", "tool capability does not match its trusted adapter")
            for permission in sorted(permissions):
                actor.require(permission)
            if not satisfies(capability["version"], adapter.require):
                raise CapabilityIncompatible(adapter.capability_id, capability["version"], adapter.require)
            # Validation has already rejected cycles and missing/incompatible
            # manifest dependencies. Execution also needs a live trusted binding.
            for dependency in capability["dependencies"]:
                dependency_name = self._capability_names.get(dependency.split("@", 1)[0])
                if dependency_name is None:
                    raise APIError(503, "CAPABILITY_DEPENDENCY_UNAVAILABLE", "tool dependency has no trusted adapter")
                checked(dependency_name)
            checked_results[tool_name] = (adapter, capability)
            return checked_results[tool_name]

        return checked(name)

    def describe(self, actor):
        tools = []
        for name in self._adapters:
            try:
                adapter, _ = self.resolve(name, actor)
            except APIError as error:
                if error.status in (403, 404):
                    continue
                raise
            tools.append({"type": "function", "function": {
                "name": adapter.name, "description": adapter.description,
                "parameters": adapter.model.model_json_schema(),
            }})
        return tools

    async def invoke(self, name, actor, request, body):
        adapter, capability = self.resolve(name, actor)
        if not isinstance(body, adapter.model):
            raise APIError(422, "INVALID_TOOL_INPUT", "tool input does not match its adapter")
        try:
            async with asyncio.timeout(capability.get("timeout_seconds", 30)):
                return await adapter.handler(request, body)
        except APIError:
            raise
        except (TimeoutError, TimeoutException):
            raise APIError(503, "CAPABILITY_TIMEOUT", "tool capability deadline exceeded") from None
        except Exception:
            # Never expose upstream bodies, request headers or exception details.
            raise APIError(503, "CAPABILITY_FAILED", "tool capability failed") from None
