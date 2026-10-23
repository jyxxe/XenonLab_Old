"""Trusted text chunker bindings; derived identity is separate from source facts.

Only authorized nonempty string values/context reach a processor. Cooperative
iteration budgets do not preempt arbitrary Python CPU work; isolation remains
Phase 3. No manifest can select an executable module or receive DB/credentials.
"""

import inspect
import logging
import math
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from types import MappingProxyType

from .capabilities import CapabilityIncompatible, load_registry, satisfies
from .common import APIError


@dataclass(frozen=True)
class ChunkerBinding:
    identity: str
    processor: Callable
    capability_id: str = "builtin.platform.worker.document-chunker"
    require: str = "^1.0.0"
    max_chunks: int = 10000
    max_chars: int = 32768
    max_tokens: int = 126
    processing_seconds: float = 10


@dataclass(frozen=True)
class ResolvedChunker:
    binding: ChunkerBinding
    processing_seconds: float
    checkpoint: Callable | None = field(default=None, repr=False, compare=False)
    actor: object = field(default=None, repr=False, compare=False)
    permissions: tuple = ("xelibrary:admin",)


class DocumentChunkers:
    def __init__(self, binding, *, registry_loader=load_registry, clock=time.monotonic, enforce=True):
        self._binding = None
        self._identities = {}
        self._used = False
        self._enabled = True
        self._loader = registry_loader
        self._clock = clock
        self.enforce = enforce
        self.replace(binding)

    def replace(self, binding):
        if self._used:
            raise ValueError("chunker replacement requires service restart")
        if not isinstance(binding.identity, str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,159}", binding.identity):
            raise ValueError("invalid derived chunker identity")
        if not callable(binding.processor) or inspect.iscoroutinefunction(binding.processor):
            raise ValueError("chunkers must be trusted synchronous iterators")
        for value, maximum in ((binding.max_chunks, 1000000), (binding.max_chars, 32768), (binding.max_tokens, 126)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError("invalid chunker output budget")
        if (isinstance(binding.processing_seconds, bool) or not isinstance(binding.processing_seconds, (int, float))
                or not math.isfinite(binding.processing_seconds) or not 0 < binding.processing_seconds <= 300):
            raise ValueError("invalid chunker processing budget")
        if binding.identity in self._identities and self._identities[binding.identity] is not binding.processor:
            raise ValueError("changed processor requires a new derived identity")
        self._binding = binding
        self._identities[binding.identity] = binding.processor

    @property
    def identity(self):
        # Reading derivative data pins the selected implementation too.
        self._used = True
        return self._binding.identity

    def set_enabled(self, enabled):
        self._enabled = bool(enabled)

    def resolve(self, actor, *, checkpoint=None):
        self._authority(actor)
        if os.getenv("CAPABILITY_LIFECYCLE_ENABLED", "0") == "1" and checkpoint is None:
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "live job authorization is unavailable")
        permissions = {"xelibrary:admin"}
        binding = self._binding
        if self.enforce and not self._enabled:
            raise APIError(404, "CAPABILITY_NOT_FOUND", "no such document chunker capability")
        budget = binding.processing_seconds
        if self.enforce:
            capability = self._loader().lookup(binding.capability_id)
            if capability is None:
                raise APIError(404, "CAPABILITY_NOT_FOUND", "no such document chunker capability")
            permissions = set(capability["permissions"])
            if capability["kind"] != "worker" or capability["runtime"] != "api" or "xelibrary:admin" not in permissions:
                raise APIError(503, "CAPABILITY_INVALID", "chunker does not match its trusted binding")
            for permission in sorted(permissions):
                actor.require(permission)
            if not satisfies(capability["version"], binding.require):
                raise CapabilityIncompatible(binding.capability_id, capability["version"], binding.require)
            if capability["dependencies"]:
                raise APIError(503, "CAPABILITY_DEPENDENCY_UNAVAILABLE", "composite document chunkers are not registered")
            declared = capability.get("timeout_seconds", budget)
            if isinstance(declared, bool) or not isinstance(declared, (int, float)) or not 0 < declared <= 300:
                raise APIError(503, "CAPABILITY_INVALID", "invalid chunker processing budget")
            budget = min(budget, declared)
        self._used = True
        return ResolvedChunker(binding, budget, checkpoint, actor, tuple(sorted(permissions)))

    @staticmethod
    def _authority(actor):
        check = getattr(actor, "require_capability", None)
        if os.getenv("CAPABILITY_LIFECYCLE_ENABLED", "0") == "1" and (
            getattr(actor, "lifecycle_revision", None) is None or not callable(check)
        ):
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "verified lifecycle snapshot required")
        if callable(check):
            check("builtin.platform.worker.index")
            check("builtin.platform.worker.document-chunker")

    def check_active(self, resolved):
        actor = resolved.checkpoint() if resolved.checkpoint else resolved.actor
        self._authority(actor)
        for permission in resolved.permissions:
            actor.require(permission)
        self.check_binding(resolved)
        return actor

    def check_binding(self, resolved):
        """Local-only check, safe inside the source publication transaction."""
        if resolved.binding is not self._binding or (self.enforce and not self._enabled):
            raise APIError(409, "CHUNKER_CHANGED", "document chunker changed or stopped")

    def chunks(self, resolved, tokenizer, values, path, table_name):
        self.check_active(resolved)
        source = MappingProxyType({column: value for column, value in values.items() if isinstance(value, str) and value.strip()})
        output = None
        exhausted = False
        elapsed = 0.0
        count = 0
        try:
            started = self._clock()
            try:
                output = iter(resolved.binding.processor(tokenizer, source, path, table_name))
            except Exception:
                raise APIError(503, "CHUNKER_FAILED", "document chunker failed") from None
            elapsed += self._clock() - started
            if elapsed > resolved.processing_seconds:
                raise APIError(503, "CHUNKER_TIMEOUT", "document chunker processing budget exceeded")
            while True:
                self.check_active(resolved)
                started = self._clock()
                try:
                    item = next(output)
                except StopIteration:
                    elapsed += self._clock() - started
                    if elapsed > resolved.processing_seconds:
                        raise APIError(503, "CHUNKER_TIMEOUT", "document chunker processing budget exceeded")
                    exhausted = True
                    return
                except Exception:
                    raise APIError(503, "CHUNKER_FAILED", "document chunker failed") from None
                count += 1
                if count > resolved.binding.max_chunks:
                    raise APIError(503, "CHUNKER_LIMIT", "document chunker output limit exceeded")
                if (not isinstance(item, (tuple, list)) or len(item) != 2 or not isinstance(item[0], str)
                        or item[0] not in source or not isinstance(item[1], str) or not item[1].strip()
                        or len(item[1]) > resolved.binding.max_chars):
                    raise APIError(503, "CHUNKER_OUTPUT_INVALID", "document chunker returned invalid output")
                try:
                    tokens = tokenizer.encode(item[1]).ids
                except Exception:
                    raise APIError(503, "CHUNKER_OUTPUT_INVALID", "document chunker returned invalid text") from None
                if not tokens or len(tokens) > resolved.binding.max_tokens:
                    raise APIError(503, "CHUNKER_OUTPUT_INVALID", "document chunker token limit exceeded")
                elapsed += self._clock() - started
                if elapsed > resolved.processing_seconds:
                    raise APIError(503, "CHUNKER_TIMEOUT", "document chunker processing budget exceeded")
                yield item[0], item[1]
        finally:
            close = getattr(output, "close", None)
            if close is not None:
                started = self._clock()
                try:
                    close()
                except Exception:
                    if exhausted:
                        raise APIError(503, "CHUNKER_FAILED", "document chunker cleanup failed") from None
                    logging.getLogger("xeonolab.search").warning("document_chunker.cleanup_failed")
                if exhausted and elapsed + self._clock() - started > resolved.processing_seconds:
                    raise APIError(503, "CHUNKER_TIMEOUT", "document chunker processing budget exceeded")
