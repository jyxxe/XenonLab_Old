"""Core-only authorization checkpoint for already-admitted trusted jobs.

The caller credential lives only in a minimal in-memory request context. It is
never serialized, logged or passed to an extension. Polling is cooperative and
not a new invocation grant, hard CPU cancellation or a shared allow cache.
"""

import os
import time

from starlette.requests import Request

from . import common
from .common import APIError

INDEX_CAPABILITY = "builtin.platform.worker.index"


class CapabilityCheckpoint:
    def __init__(self, request, actor, *, capabilities, permissions, clock=time.monotonic, interval=5,
                 require_lifecycle=True):
        if type(interval) not in (int, float) or not 0 < interval <= 5:
            raise ValueError("checkpoint interval must be within five seconds")
        if type(require_lifecycle) is not bool:
            raise ValueError("checkpoint lifecycle requirement must be boolean")
        self._lifecycle_required = require_lifecycle
        self._context = Request({
            "type": "http", "app": request.app,
            "headers": [(b"authorization", request.headers.get("Authorization", "").encode("latin-1"))],
        })
        self._subject, self._kind = actor.sub, actor.kind
        self._actor = actor
        self._capabilities, self._permissions = tuple(capabilities), tuple(permissions)
        self._clock, self._interval = clock, interval
        self._next_refresh = 0.0
        self._refresh = getattr(common, "refresh_identity", None)
        if not callable(self._refresh):
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "live job authorization is unavailable")
        self._validate(actor)

    def _validate(self, actor):
        if (not isinstance(actor.sub, str) or not actor.sub.strip()
                or not isinstance(actor.kind, str) or not actor.kind.strip()):
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "verified job identity required")
        if actor.sub != self._subject or actor.kind != self._kind:
            raise APIError(403, "JOB_IDENTITY_CHANGED", "job identity changed")
        revision = getattr(actor, "lifecycle_revision", None)
        check = getattr(actor, "require_capability", None)
        # Once a verified lifecycle protocol is seen, never downgrade to the
        # pre-rollout identity-only path on a later refresh.
        if revision is not None:
            self._lifecycle_required = True
        if self._lifecycle_required and (type(revision) is not int or revision < 0 or not callable(check)):
            raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "verified job lifecycle snapshot required")
        for permission in self._permissions:
            actor.require(permission)
        for identifier in self._capabilities:
            if callable(check):
                check(identifier)

    def start(self):
        # A queued task must not run with only the original admission snapshot.
        return self._check(force=True)

    def __call__(self):
        return self._check(force=False)

    def _check(self, *, force):
        now = self._clock()
        if force or now >= self._next_refresh:
            try:
                actor = self._refresh(self._context)
            except APIError:
                raise
            except Exception:
                raise APIError(503, "CAPABILITY_LIFECYCLE_UNAVAILABLE", "live job authorization unavailable") from None
            self._validate(actor)
            self._actor = actor
            self._next_refresh = self._clock() + self._interval
        self._validate(self._actor)
        return self._actor


def index_checkpoint(request, actor):
    mode = os.getenv("CAPABILITY_LIFECYCLE_ENABLED", "0")
    if mode not in {"0", "1"}:
        raise ValueError("CAPABILITY_LIFECYCLE_ENABLED must be 0 or 1")
    verified = getattr(actor, "lifecycle_revision", None) is not None
    if mode == "0" and not verified:
        return None  # Pre-rollout compatibility only, never a strict fallback.
    return CapabilityCheckpoint(request, actor, capabilities=(INDEX_CAPABILITY,), permissions=("xelibrary:admin",))
