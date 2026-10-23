"""Bounded, disposable candidate embeddings; never an authorization cache."""

import threading
import time
from collections import OrderedDict


class QualityCache:
    def __init__(self, max_entries=2048, max_bytes=32 * 1024 * 1024, ttl=300, clock=time.monotonic):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.clock = clock
        self.entries = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()

    def _remove(self, key):
        _, _, size = self.entries.pop(key)
        self.bytes -= size

    def get(self, key):
        with self.lock:
            item = self.entries.get(key)
            if item is None:
                return None
            at, vector, _ = item
            if self.clock() - at >= self.ttl:
                self._remove(key)
                return None
            self.entries.move_to_end(key)
            return list(vector)

    def put(self, key, vector):
        # Account conservatively for Python float/tuple storage as well as keys.
        size = len(repr(key).encode()) + 64 + 40 * len(vector)
        evicted = 0
        with self.lock:
            if key in self.entries:
                self._remove(key)
            if size > self.max_bytes or self.max_entries < 1:
                return evicted
            self.entries[key] = (self.clock(), tuple(vector), size)
            self.bytes += size
            while len(self.entries) > self.max_entries or self.bytes > self.max_bytes:
                self._remove(next(iter(self.entries)))
                evicted += 1
        return evicted

    def invalidate(self, table_id, row_id=None):
        with self.lock:
            for key in list(self.entries):
                if key[3] == table_id and (row_id is None or key[4] == row_id):
                    self._remove(key)

    def count(self):
        with self.lock:
            return len(self.entries)
