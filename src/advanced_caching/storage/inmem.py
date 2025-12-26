from __future__ import annotations

import threading
import time
from typing import Any

from .utils import CacheEntry


class InMemCache:
    """Thread-safe in-memory cache with TTL support."""

    def __init__(self):
        self._data: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def _make_entry(self, value: Any, ttl: int) -> CacheEntry:
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")
        return CacheEntry(value=value, fresh_until=fresh_until, created_at=now)

    def get(self, key: str) -> Any | None:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            if time.time() >= entry.fresh_until:
                del self._data[key]
                return None
            return entry.value

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        entry = self._make_entry(value, ttl)
        with self._lock:
            self._data[key] = entry

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def get_entry(self, key: str) -> CacheEntry | None:
        with self._lock:
            return self._data.get(key)

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        if ttl is not None:
            entry = self._make_entry(entry.value, ttl)
        with self._lock:
            self._data[key] = entry

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        with self._lock:
            now = time.time()
            if key in self._data and self._data[key].is_fresh(now):
                return False
            entry = self._make_entry(value, ttl)
            self._data[key] = entry
            return True

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def cleanup_expired(self) -> int:
        with self._lock:
            now = time.time()
            expired_keys = [k for k, e in self._data.items() if e.fresh_until < now]
            for k in expired_keys:
                del self._data[k]
            return len(expired_keys)

    @property
    def lock(self):
        return self._lock
