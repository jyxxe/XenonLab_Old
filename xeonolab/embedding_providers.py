"""Trusted static embedding providers; no model loading from plugin manifests.

Read/encode capabilities are separate to preserve write-only encoder clients.
D100M keeps its trusted synchronous CPU execution; Qwen retains its existing
process timeout/termination. This module does not invent thread cancellation.
"""

import logging
import math
import os
from dataclasses import dataclass
from typing import Protocol

from .capabilities import CapabilityIncompatible, load_registry, satisfies
from .common import APIError
from .qwen_encoder import MODEL_ID


@dataclass(frozen=True)
class ProviderState:
    configured: bool
    busy: bool
    revision: str | None
    status: str


@dataclass(frozen=True)
class EmbeddingResult:
    model: str
    metadata: dict
    vectors: list[list[float]]
    tokens: int

    def payload(self, count):
        if (not isinstance(self.model, str) or not self.model or type(self.tokens) is not int or self.tokens < 0
                or len(self.vectors) != count or not isinstance(self.metadata.get("model_revision"), str)
                or not self.metadata["model_revision"]):
            raise ValueError("invalid embedding result")
        dimensions = self.metadata.get("dimensions")
        for vector in self.vectors:
            if not vector or (dimensions is not None and len(vector) != dimensions):
                raise ValueError("invalid embedding dimensions")
            if any(type(value) not in (int, float) or not math.isfinite(value) for value in vector):
                raise ValueError("invalid embedding vector")
        return {
            "object": "list", "model": self.model,
            **{key: self.metadata[key] for key in ("model_revision", "dimensions", "preprocessing_revision", "normalized") if key in self.metadata},
            "data": [{"index": index, "object": "embedding", "embedding": vector} for index, vector in enumerate(self.vectors)],
            "usage": {"input_tokens": self.tokens, "total_tokens": self.tokens},
        }


class PreparedEmbedding(Protocol):
    def encode(self, texts, purpose, truncation, expected, image=None) -> EmbeddingResult: ...


class EmbeddingProvider(Protocol):
    def descriptor(self) -> dict: ...
    def state(self) -> ProviderState: ...
    def prepare(self) -> PreparedEmbedding: ...
    def start(self) -> None: ...
    def close(self) -> None: ...


class D100MProvider:
    def __init__(self, worker):
        self._worker = worker

    def descriptor(self):
        state = self.state()
        return {"id": "D100M-Embedding", "alias": "fast", "dimensions": 256, "max_tokens": 128,
                "normalized": True, "status": state.status, "revision": state.revision}

    def state(self):
        worker = self._worker()
        lock = getattr(worker, "lock", None)
        return ProviderState(worker is not None, bool(lock and lock.locked()), worker.revision if worker else None,
                             "ready" if worker else "not_configured")

    def prepare(self):
        worker = self._worker()
        if worker is None:
            raise APIError(503, "MODEL_NOT_CONFIGURED", "D100M weights not configured")
        return _D100MCall(worker)

    def start(self):
        pass

    def close(self):
        pass


@dataclass(frozen=True)
class _D100MCall:
    worker: object

    def encode(self, texts, purpose, truncation, expected, image=None):
        vectors, tokens = self.worker.encode(texts, truncation)
        return EmbeddingResult("D100M-Embedding", {"model_revision": self.worker.revision}, vectors, tokens)


class QwenProvider:
    def __init__(self, worker):
        self._worker = worker
        self._owned_worker = None

    def descriptor(self):
        return self._worker().descriptor()

    def state(self):
        worker = self._worker()
        descriptor = worker.descriptor()
        return ProviderState(descriptor["status"] == "ready", worker.lock.locked(),
                             descriptor.get("model_revision"), descriptor["status"])

    def prepare(self):
        return _QwenCall(self._worker())

    def start(self):
        self._owned_worker = self._worker()
        self._owned_worker.start()

    def close(self):
        if self._owned_worker is not None:
            self._owned_worker.close()
            self._owned_worker = None


@dataclass(frozen=True)
class _QwenCall:
    worker: object

    def encode(self, texts, purpose, truncation, expected, image=None):
        kwargs = {"expected": expected}
        if image is not None:
            kwargs["image"] = image
        vectors, tokens = self.worker.encode(texts, purpose, truncation, **kwargs)
        descriptor = self.worker.descriptor()
        return EmbeddingResult(MODEL_ID, {key: descriptor[key] for key in (
            "model_revision", "dimensions", "preprocessing_revision", "normalized")}, vectors, tokens)


@dataclass(frozen=True)
class ProviderBinding:
    slot: str
    aliases: tuple[str, ...]
    model_id: str
    metric_label: str
    capability_id: str
    runtime: str
    provider: EmbeddingProvider
    images: bool = False
    require: str = "^1.0.0"
    queue_label: str | None = None

    @property
    def history_label(self):
        return self.queue_label or self.metric_label


class EmbeddingProviders:
    def __init__(self, bindings, *, registry_loader=load_registry, enforce=True, lifecycle_required=None):
        self._bindings = {}
        self._aliases = {}
        self._disabled = set()
        self._faulted = set()
        self._started = False
        self._owned = []
        self._loader = registry_loader
        self.enforce = enforce
        mode = os.getenv("CAPABILITY_LIFECYCLE_ENABLED", "0")
        if mode not in {"0", "1"}:
            raise ValueError("CAPABILITY_LIFECYCLE_ENABLED must be 0 or 1")
        self._lifecycle_required = mode == "1" if lifecycle_required is None else lifecycle_required
        for binding in bindings:
            self.replace(binding)

    def replace(self, binding):
        if self._started:
            raise ValueError("embedding provider replacement requires service restart")
        if (not isinstance(binding.aliases, tuple) or not binding.aliases
                or any(not isinstance(alias, str) or not alias for alias in binding.aliases)
                or len(set(binding.aliases)) != len(binding.aliases)):
            raise ValueError("invalid embedding provider aliases")
        if any(alias in self._aliases and self._aliases[alias] != binding.slot for alias in binding.aliases):
            raise ValueError("duplicate or missing embedding provider aliases")
        self._aliases = {alias: slot for alias, slot in self._aliases.items() if slot != binding.slot}
        self._bindings[binding.slot] = binding
        self._aliases.update({alias: binding.slot for alias in binding.aliases})
        self._faulted.discard(binding.slot)

    def binding(self, alias):
        slot = self._aliases.get(alias)
        if slot is None:
            raise APIError(422, "UNKNOWN_MODEL", "unsupported embedding model")
        return self._bindings[slot]

    def metric_label(self, alias):
        try:
            return self.binding(alias).metric_label
        except APIError:
            return "unknown"

    def set_enabled(self, slot, enabled):
        if slot not in self._bindings:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such embedding capability")
        if enabled:
            self._disabled.discard(slot)
        else:
            self._disabled.add(slot)

    def require_lifecycle(self, binding, actor):
        check = getattr(actor, "require_capability", None)
        revision = getattr(actor, "lifecycle_revision", None)
        if self._lifecycle_required and (not callable(check) or type(revision) is not int or revision < 0):
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "verified lifecycle snapshot required")
        if callable(check):
            # Availability gate only: it must not require the read scope when
            # the caller is using the separately authorized encode capability.
            check(binding.capability_id)

    def _resolve(self, binding, actor, operation):
        if actor is not None:
            self.require_lifecycle(binding, actor)
        if binding.slot in self._faulted and operation == "encode":
            raise APIError(503, "MODEL_UNAVAILABLE", "embedding provider failed to start")
        if not self.enforce:
            return binding
        if binding.slot in self._disabled:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such embedding capability")
        identifier = binding.capability_id + ("-encode" if operation == "encode" else "")
        capability = self._loader().lookup(identifier)
        if capability is None:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such embedding capability")
        minimum = "embedding:write" if operation == "encode" else "embedding:read"
        permissions = set(capability["permissions"])
        if capability["kind"] != "provider" or capability["runtime"] != binding.runtime or minimum not in permissions:
            raise APIError(503, "CAPABILITY_INVALID", "embedding capability does not match its trusted binding")
        if actor is not None:
            for permission in sorted(permissions):
                actor.require(permission)
        if not satisfies(capability["version"], binding.require):
            raise CapabilityIncompatible(identifier, capability["version"], binding.require)
        if operation == "encode" and capability.get("timeout_seconds") is not None:
            raise APIError(503, "CAPABILITY_INVALID", "embedding execution budgets belong to the worker configuration")
        if capability["dependencies"]:
            raise APIError(503, "CAPABILITY_DEPENDENCY_UNAVAILABLE", "composite embedding providers are not registered")
        return binding

    def models(self, actor, *, validate=None):
        result = []
        for binding in self._bindings.values():
            try:
                self._resolve(binding, actor, "describe")
            except APIError as error:
                if error.status in (403, 404):
                    continue
                raise
            try:
                if binding.slot in self._faulted:
                    raise ValueError("provider startup failed")
                descriptor = binding.provider.descriptor()
                if validate is not None:
                    validate(descriptor)
                result.append(descriptor)
            except Exception:
                result.append({"id": binding.model_id, "alias": binding.slot, "status": "unavailable"})
        return result

    def prepare(self, alias, actor, *, image=False):
        binding = self.binding(alias)
        if image and not binding.images:
            raise APIError(422, "UNKNOWN_MODEL", "image encoding requires the quality model")
        self._resolve(binding, actor, "encode")
        try:
            return binding, binding.provider.prepare()
        except APIError:
            raise
        except Exception:
            raise APIError(500, "INTERNAL_ERROR", "embedding provider failed") from None

    @staticmethod
    def encode(call, texts, purpose, truncation, expected, image=None):
        try:
            return call.encode(texts, purpose, truncation, expected, image)
        except APIError:
            raise
        except Exception:
            raise APIError(500, "INTERNAL_ERROR", "embedding provider failed") from None

    def states(self):
        states = []
        for binding in self._bindings.values():
            try:
                state = binding.provider.state()
                if binding.slot in self._faulted:
                    state = ProviderState(False, False, None, "unavailable")
                elif self.enforce and binding.slot in self._disabled:
                    state = ProviderState(state.configured, state.busy, state.revision, "disabled")
            except Exception:
                state = ProviderState(False, False, None, "unavailable")
            states.append((binding, state))
        return states

    def primary_state(self):
        return next((state for binding, state in self.states() if binding.slot == "fast"),
                    ProviderState(False, False, None, "not_configured"))

    def ready(self):
        try:
            binding = self.binding("fast")
            self._resolve(binding, None, "encode")
            return binding.provider.state().configured
        except Exception:
            return False

    def start(self):
        self._started = True
        for binding in self._bindings.values():
            self._owned.append(binding)
            try:
                binding.provider.start()
                self._faulted.discard(binding.slot)
            except Exception:
                self._faulted.add(binding.slot)
                logging.getLogger("xeonolab.embedding").warning("embedding.provider.start_failed slot=%s", binding.slot)

    def close(self):
        for binding in reversed(self._owned):
            try:
                binding.provider.close()
            except Exception:
                logging.getLogger("xeonolab.embedding").warning("embedding.provider.close_failed slot=%s", binding.slot)
        self._owned.clear()
        self._started = False
