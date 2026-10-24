"""Trusted static Lean worker binding, never a manifest-selected executable.

Toolchain trust, sandboxing, subprocess limits and ownership remain in the
Lean service. The continuation callback contains no credential value for the
worker or child environment; only trusted core code retains the request context.
"""

import inspect
import os
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import capabilities
from .capability_checkpoint import CapabilityCheckpoint
from .common import APIError

LEAN_CAPABILITY = 'builtin.platform.worker.lean-verifier'


def authority_configured(app):
    return (app.state.settings.mode == 'development'
            or callable(getattr(app.state, 'authorization_resolver', None))
            or bool(os.getenv('AUTHORIZATION_URL')))


def validate_authority_configuration(settings):
    endpoint = os.getenv('AUTHORIZATION_URL')
    if settings.mode != 'development' and endpoint:
        try:
            parsed = urlsplit(endpoint)
            if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                    or parsed.password is not None or parsed.query or parsed.fragment
                    or any(character.isspace() for character in endpoint)):
                raise ValueError
            _ = parsed.port
        except ValueError:
            raise ValueError('Lean authorization requires a fixed HTTPS endpoint without credentials') from None


@dataclass(frozen=True)
class LeanCheckpoint:
    _authorization: CapabilityCheckpoint
    generation: int

    def start(self):
        return self._authorization.start()

    def __call__(self):
        return self._authorization()


class LeanWorkers:
    def __init__(self, runner, *, maximum_timeout, registry_loader=None):
        # A replacement is trusted platform code with the same cooperative
        # continuation protocol, not code imported from a declaration.
        try:
            if inspect.iscoroutinefunction(runner) or inspect.iscoroutinefunction(getattr(runner, '__call__', None)):
                raise ValueError
            inspect.signature(runner).bind(None, None, None, None, None, check_active=None)
        except (ValueError, TypeError):
            raise ValueError('Lean worker must implement the trusted continuation protocol') from None
        mode = os.getenv('CAPABILITY_LIFECYCLE_ENABLED', '0')
        if mode not in {'0', '1'}:
            raise ValueError('CAPABILITY_LIFECYCLE_ENABLED must be 0 or 1')
        self._lifecycle_required = mode == '1'
        self._runner = runner
        self._maximum_timeout = maximum_timeout
        self._registry_loader = registry_loader or capabilities.load_registry
        self._enabled = True
        self._generation = 0
        self._lock = threading.Lock()

    def set_enabled(self, enabled):
        """Trusted process-local hook; no HTTP management or global claim."""
        if type(enabled) is not bool:
            raise ValueError('Lean worker enablement must be boolean')
        with self._lock:
            if enabled != self._enabled:
                self._generation += 1
                self._enabled = enabled

    def _capability(self, expected_generation=None):
        with self._lock:
            if not self._enabled or (expected_generation is not None and expected_generation != self._generation):
                raise APIError(404, 'CAPABILITY_NOT_FOUND', 'no such Lean worker capability')
        try:
            item = self._registry_loader().lookup(LEAN_CAPABILITY)
            if item is None:
                raise APIError(404, 'CAPABILITY_NOT_FOUND', 'no such Lean worker capability')
            if (item['kind'] != 'worker' or item['runtime'] != 'service'
                    or 'lean:verify' not in item['permissions']):
                raise ValueError
            if not capabilities.satisfies(item['version'], '^1.0.0'):
                raise capabilities.CapabilityIncompatible(LEAN_CAPABILITY, item['version'], '^1.0.0')
            if item['dependencies']:
                raise APIError(503, 'CAPABILITY_DEPENDENCY_UNAVAILABLE', 'no trusted Lean dependency binding')
            timeout = item.get('timeout_seconds', 30)
            if type(timeout) is not int or not 1 <= timeout <= self._maximum_timeout:
                raise ValueError
            return item
        except (ValueError, KeyError, TypeError, AttributeError, OSError):
            raise APIError(503, 'CAPABILITY_INVALID', 'Lean worker contract unavailable') from None

    def resolve(self, actor, timeout, *, generation=None):
        actor.require('lean:verify')
        revision = getattr(actor, 'lifecycle_revision', None)
        check = getattr(actor, 'require_capability', None)
        if self._lifecycle_required and (type(revision) is not int or revision < 0 or not callable(check)):
            raise APIError(503, 'CAPABILITY_LIFECYCLE_UNAVAILABLE', 'verified lifecycle snapshot required')
        if callable(check):
            check(LEAN_CAPABILITY)
        item = self._capability(generation)
        for permission in item['permissions']:
            actor.require(permission)
        if timeout > item.get('timeout_seconds', 30):
            raise APIError(422, 'LEAN_TIMEOUT_UNSUPPORTED', 'requested timeout exceeds worker capability budget')
        return self._runner

    def checkpoint(self, request, actor):
        if not authority_configured(request.app):
            raise APIError(503, 'AUTHORIZATION_NOT_CONFIGURED', 'live account authority required for Lean work')
        with self._lock:
            generation = self._generation
        authorization = CapabilityCheckpoint(
            request, actor, capabilities=(LEAN_CAPABILITY,), permissions=('lean:verify',),
            require_lifecycle=self._lifecycle_required or getattr(actor, 'lifecycle_revision', None) is not None,
        )
        return LeanCheckpoint(authorization, generation)

    def ready(self):
        try:
            self._capability()
            return True
        except APIError:
            return False
