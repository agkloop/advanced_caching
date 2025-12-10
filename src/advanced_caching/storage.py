"""
Storage backends for caching.

Provides InMemCache (in-memory), RedisCache, HybridCache, and the CacheStorage protocol.
All storage backends implement the CacheStorage protocol for composability.
"""

from __future__ import annotations

import pickle
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol

try:
    import redis
except ImportError:
    redis = None  # type: ignore


# ============================================================================
# Cache Entry - Internal data structure
# ============================================================================


@dataclass
class CacheEntry:
    """Internal cache entry with TTL support."""

    value: Any
    fresh_until: float  # Unix timestamp
    created_at: float

    def is_fresh(self) -> bool:
        """Check if entry is still fresh."""
        return time.time() < self.fresh_until

    def age(self) -> float:
        """Get age of entry in seconds."""
        return time.time() - self.created_at


# ============================================================================
# Storage Protocol - Common interface for all backends
# ============================================================================


class CacheStorage(Protocol):
    """
    Protocol for cache storage backends.

    All cache implementations (InMemCache, RedisCache, HybridCache)
    must implement these methods to be compatible with decorators.

    This enables composability - you can swap storage backends without
    changing your caching logic.

    Example:
        def my_custom_cache():
            '''Any class implementing these methods works!'''
            def get(self, key: str) -> Any | None: ...
            def set(self, key: str, value: Any, ttl: int = 0) -> None: ...
            # ... implement other methods
    """

    def get(self, key: str) -> Any | None:
        """Get value by key. Returns None if not found or expired."""
        ...

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Set value with TTL in seconds. ttl=0 means no expiration."""
        ...

    def delete(self, key: str) -> None:
        """Delete key from cache."""
        ...

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        ...

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """
        Atomic set if not exists. Returns True if set, False if already exists.
        Used for distributed locking.
        """
        ...


def validate_cache_storage(cache: Any) -> bool:
    """
    Validate that an object implements the CacheStorage protocol.
    Useful for debugging custom cache implementations.

    Returns:
        True if valid, False otherwise
    """
    required_methods = ["get", "set", "delete", "exists", "set_if_not_exists"]
    return all(
        hasattr(cache, method) and callable(getattr(cache, method))
        for method in required_methods
    )


# ============================================================================
# InMemCache - In-memory storage with TTL
# ============================================================================


class InMemCache:
    """
    Thread-safe in-memory cache with TTL support.

    Attributes:
        _data: internal entry map
        _lock: re-entrant lock to protect concurrent access
    """

    def __init__(self):
        self._data: dict[str, CacheEntry] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Any | None:
        """Return value if key still fresh, otherwise drop it."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None

            if not entry.is_fresh():
                del self._data[key]
                return None

            return entry.value

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Store value for ttl seconds (0=forever)."""
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")

        entry = CacheEntry(value=value, fresh_until=fresh_until, created_at=now)

        with self._lock:
            self._data[key] = entry

    def delete(self, key: str) -> None:
        """Delete key from cache."""
        with self._lock:
            self._data.pop(key, None)

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return self.get(key) is not None

    def get_entry(self, key: str) -> CacheEntry | None:
        """Get raw entry (for advanced usage like SWR)."""
        with self._lock:
            return self._data.get(key)

    def set_entry(self, key: str, entry: CacheEntry) -> None:
        """Set raw entry (for advanced usage like SWR)."""
        with self._lock:
            self._data[key] = entry

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """Atomic set if not exists. Returns True if set, False if exists."""
        with self._lock:
            if key in self._data and self._data[key].is_fresh():
                return False
            self.set(key, value, ttl)
            return True

    def clear(self) -> None:
        """Clear all cached data."""
        with self._lock:
            self._data.clear()

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count of removed entries."""
        with self._lock:
            now = time.time()
            expired_keys = [
                key for key, entry in self._data.items() if entry.fresh_until < now
            ]
            for key in expired_keys:
                del self._data[key]
            return len(expired_keys)

    @property
    def lock(self):
        """Get the internal lock (for advanced usage)."""
        return self._lock


# ============================================================================
# RedisCache - Redis-backed storage
# ============================================================================


class RedisCache:
    """
    Redis-backed cache storage.
    Supports TTL, distributed locking, and persistence.

    Example:
        import redis
        client = redis.Redis(host='localhost', port=6379)
        cache = RedisCache(client, prefix="app:")
        cache.set("user:123", {"name": "John"}, ttl=60)
    """

    def __init__(self, redis_client: Any, prefix: str = ""):
        """
        Initialize Redis cache.

        Args:
            redis_client: Redis client instance
            prefix: Key prefix for namespacing
        """
        if redis is None:
            raise ImportError("redis package required. Install: pip install redis")
        self.client = redis_client
        self.prefix = prefix

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Any | None:
        """Get value by key."""
        try:
            data = self.client.get(self._make_key(key))
            if data is None:
                return None
            return pickle.loads(data)
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Set value with optional TTL in seconds."""
        try:
            data = pickle.dumps(value)
            if ttl > 0:
                self.client.setex(self._make_key(key), ttl, data)
            else:
                self.client.set(self._make_key(key), data)
        except Exception as e:
            raise RuntimeError(f"Redis set failed: {e}")

    def delete(self, key: str) -> None:
        """Delete key from cache."""
        try:
            self.client.delete(self._make_key(key))
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        """Check if key exists."""
        try:
            return bool(self.client.exists(self._make_key(key)))
        except Exception:
            return False

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """Atomic set if not exists."""
        try:
            data = pickle.dumps(value)
            result = self.client.set(
                self._make_key(key), data, ex=ttl if ttl > 0 else None, nx=True
            )
            return bool(result)
        except Exception:
            return False


# ============================================================================
# HybridCache - L1 (memory) + L2 (Redis) cache
# ============================================================================


class HybridCache:
    """
    Two-level cache: L1 (InMemCache) + L2 (RedisCache).
    Fast reads from memory, distributed persistence in Redis.

    Example:
        import redis
        client = redis.Redis()
        cache = HybridCache(
            l1_cache=InMemCache(),
            l2_cache=RedisCache(client),
            l1_ttl=60
        )
    """

    def __init__(
        self,
        l1_cache: CacheStorage | None = None,
        l2_cache: CacheStorage | None = None,
        l1_ttl: int = 60,
    ):
        """
        Initialize hybrid cache.

        Args:
            l1_cache: L1 cache (memory), defaults to InMemCache
            l2_cache: L2 cache (distributed), required
            l1_ttl: TTL for L1 cache in seconds
        """
        self.l1 = l1_cache if l1_cache is not None else InMemCache()
        if l2_cache is None:
            raise ValueError("l2_cache is required for HybridCache")
        self.l2 = l2_cache
        self.l1_ttl = l1_ttl

    def get(self, key: str) -> Any | None:
        """Get value, checking L1 then L2."""
        # Try L1 first
        value = self.l1.get(key)
        if value is not None:
            return value

        # Try L2
        value = self.l2.get(key)
        if value is not None:
            # Populate L1
            self.l1.set(key, value, self.l1_ttl)

        return value

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Set value in both L1 and L2."""
        self.l1.set(key, value, min(ttl, self.l1_ttl) if ttl > 0 else self.l1_ttl)
        self.l2.set(key, value, ttl)

    def delete(self, key: str) -> None:
        """Delete from both caches."""
        self.l1.delete(key)
        self.l2.delete(key)

    def exists(self, key: str) -> bool:
        """Check if key exists in either cache."""
        return self.l1.exists(key) or self.l2.exists(key)

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """Atomic set if not exists (L2 only for consistency)."""
        success = self.l2.set_if_not_exists(key, value, ttl)
        if success:
            self.l1.set(key, value, min(ttl, self.l1_ttl) if ttl > 0 else self.l1_ttl)
        return success
