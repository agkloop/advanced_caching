from __future__ import annotations

import sys
import threading
import time
from typing import Any

from .utils import CacheEntry


class InMemCache:
    """Thread-safe in-memory cache with TTL support.

    Hot-path design notes
    ---------------------
    * ``get`` / ``get_entry`` use an *optimistic lock-free read*: Python's GIL
      guarantees ``dict.get`` is atomic at the C level, so we read without
      acquiring the lock.  The lock is only needed when we must *delete* a
      stale entry (write path).
    * ``get_entry`` accepts an optional pre-computed ``now`` so callers that
      already hold a timestamp can avoid a second ``time.time()`` call.
    * ``set`` / ``set_entry`` take the lock once and write in one step.
    """

    __slots__ = ("_data", "_lock")

    def __init__(self) -> None:
        self._data: dict[str, CacheEntry] = {}
        self._lock = threading.Lock()

    # ── read ──────────────────────────────────────────────────────────────────

    def get(self, key: str) -> Any | None:
        # Lock-free read — GIL makes dict.get atomic
        entry = self._data.get(key)
        if entry is None:
            return None
        if time.time() < entry.fresh_until:
            return entry.value
        # Stale: evict under lock (double-check to avoid redundant deletes)
        with self._lock:
            entry = self._data.get(key)
            if entry is not None and time.time() >= entry.fresh_until:
                del self._data[key]
        return None

    def get_entry(self, key: str, now: float | None = None) -> CacheEntry | None:
        """Return the raw ``CacheEntry`` — used by SWR for staleness checks.

        Returns the entry even if stale so callers (e.g., SWR) can read
        ``entry.fresh_until`` and ``entry.created_at`` to decide whether to
        serve stale data and schedule a background refresh.

        The optional *now* parameter is accepted for API compatibility but
        is not used for freshness filtering here.
        """
        return self._data.get(key)

    def exists(self, key: str) -> bool:
        entry = self._data.get(key)
        if entry is None:
            return False
        return time.time() < entry.fresh_until

    # ── write ─────────────────────────────────────────────────────────────────

    def set(self, key: str, value: Any, ttl: int | float = 0) -> None:
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")
        entry = CacheEntry(value=value, fresh_until=fresh_until, created_at=now)
        with self._lock:
            self._data[key] = entry

    def set_entry(
        self, key: str, entry: CacheEntry, ttl: int | float | None = None
    ) -> None:
        if ttl is not None:
            now = time.time()
            fresh_until = now + ttl if ttl > 0 else float("inf")
            entry = CacheEntry(
                value=entry.value, fresh_until=fresh_until, created_at=now
            )
        with self._lock:
            self._data[key] = entry

    def set_if_not_exists(self, key: str, value: Any, ttl: int | float) -> bool:
        # Fast lock-free check first
        entry = self._data.get(key)
        if entry is not None and time.time() < entry.fresh_until:
            return False
        with self._lock:
            entry = self._data.get(key)
            if entry is not None and time.time() < entry.fresh_until:
                return False
            now = time.time()
            fresh_until = now + ttl if ttl > 0 else float("inf")
            self._data[key] = CacheEntry(
                value=value, fresh_until=fresh_until, created_at=now
            )
            return True

    def delete(self, key: str) -> None:
        with self._lock:
            self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    # ── maintenance ───────────────────────────────────────────────────────────

    def cleanup_expired(self) -> int:
        with self._lock:
            now = time.time()
            expired = [k for k, e in self._data.items() if e.fresh_until <= now]
            for k in expired:
                del self._data[k]
            return len(expired)

    def get_memory_usage(self) -> dict[str, Any]:
        """Approximate memory usage of the cache (shallow sizing)."""
        with self._lock:
            if not self._data:
                return {"bytes_used": 0, "entry_count": 0, "avg_entry_size": 0}
            total = sys.getsizeof(self._data)
            for key, entry in self._data.items():
                total += sys.getsizeof(key)
                total += sys.getsizeof(entry)
                total += sys.getsizeof(entry.value)
            count = len(self._data)
            return {
                "bytes_used": total,
                "entry_count": count,
                "avg_entry_size": total // count,
            }

    @property
    def lock(self) -> threading.Lock:
        return self._lock
