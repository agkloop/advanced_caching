"""
Storage backends for caching.

Provides InMemCache (in-memory), RedisCache, HybridCache, and the CacheStorage protocol.
All storage backends implement the CacheStorage protocol for composability.
"""

from __future__ import annotations

import json
import math
import pickle
import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol
import orjson

try:
    import redis
except ImportError:
    redis = None  # type: ignore


class Serializer(Protocol):
    """Simple serializer protocol used by RedisCache."""

    def dumps(self, obj: Any) -> bytes: ...

    def loads(self, data: bytes) -> Any: ...


class PickleSerializer:
    """Pickle serializer using highest protocol (fastest, flexible)."""

    __slots__ = ()
    handles_entries = True

    @staticmethod
    def dumps(obj: Any) -> bytes:
        return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)

    @staticmethod
    def loads(data: bytes) -> Any:
        return pickle.loads(data)


class JsonSerializer:
    """JSON serializer for text-friendly payloads (wraps CacheEntry). Uses orjson"""

    __slots__ = ()
    handles_entries = False

    @staticmethod
    def dumps(obj: Any) -> bytes:
        return orjson.dumps(obj)

    @staticmethod
    def loads(data: bytes) -> Any:
        return orjson.loads(data)


_BUILTIN_SERIALIZERS: dict[str, Serializer] = {
    "pickle": PickleSerializer(),
    "json": JsonSerializer(),
}


# ============================================================================
# Cache Entry - Internal data structure
# ============================================================================


@dataclass(slots=True)
class CacheEntry:
    """Internal cache entry with TTL support."""

    value: Any
    fresh_until: float  # Unix timestamp
    created_at: float

    def is_fresh(self, now: float | None = None) -> bool:
        """Check if entry is still fresh."""
        if now is None:
            now = time.time()
        return now < self.fresh_until

    def age(self, now: float | None = None) -> float:
        """Get age of entry in seconds."""
        if now is None:
            now = time.time()
        return now - self.created_at


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

    def get_entry(self, key: str) -> "CacheEntry | None":
        """Get raw cache entry (may be stale)."""
        ...

    def set_entry(self, key: str, entry: "CacheEntry", ttl: int | None = None) -> None:
        """Store raw cache entry, optionally overriding TTL."""
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
    required_methods = [
        "get",
        "set",
        "delete",
        "exists",
        "set_if_not_exists",
        "get_entry",
        "set_entry",
    ]
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

    def _make_entry(self, value: Any, ttl: int) -> CacheEntry:
        """Create a cache entry with computed freshness window."""
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")
        return CacheEntry(value=value, fresh_until=fresh_until, created_at=now)

    def get(self, key: str) -> Any | None:
        """Return value if key still fresh, otherwise drop it."""
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None

            if time.time() >= entry.fresh_until:
                del self._data[key]
                return None

            return entry.value

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Store value for ttl seconds (0=forever)."""
        entry = self._make_entry(value, ttl)

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
        """Get raw entry (can be stale)."""
        with self._lock:
            return self._data.get(key)

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        """Set raw entry; optional ttl overrides entry freshness."""
        if ttl is not None:
            entry = self._make_entry(entry.value, ttl)
        with self._lock:
            self._data[key] = entry

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """Atomic set if not exists. Returns True if set, False if exists."""
        with self._lock:
            now = time.time()
            if key in self._data and self._data[key].is_fresh(now):
                return False
            entry = self._make_entry(value, ttl)
            self._data[key] = entry
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

    def __init__(
        self,
        redis_client: Any,
        prefix: str = "",
        serializer: str | Serializer | None = "pickle",
    ):
        """
        Initialize Redis cache.

        Args:
            redis_client: Redis client instance
            prefix: Key prefix for namespacing
            serializer: Built-in name ("pickle" | "json" | "msgpack"), or
                any object with ``dumps(obj)->bytes`` and ``loads(bytes)->Any``.
        """
        if redis is None:
            raise ImportError("redis package required. Install: pip install redis")
        self.client = redis_client
        self.prefix = prefix
        self._serializer, self._wrap_entries = self._resolve_serializer(serializer)

    @staticmethod
    def _wrap_payload(obj: Any) -> Any:
        if isinstance(obj, CacheEntry):
            return {
                "__ac_type": "entry",
                "v": obj.value,
                "f": obj.fresh_until,
                "c": obj.created_at,
            }
        return {"__ac_type": "value", "v": obj}

    @staticmethod
    def _unwrap_payload(obj: Any) -> Any:
        if isinstance(obj, dict):
            obj_type = obj.get("__ac_type")
            if obj_type == "entry":
                return CacheEntry(
                    value=obj.get("v"),
                    fresh_until=float(obj.get("f", 0.0)),
                    created_at=float(obj.get("c", 0.0)),
                )
            if obj_type == "value":
                return obj.get("v")
        return obj

    def _serialize(self, obj: Any) -> bytes:
        if self._wrap_entries:
            return self._serializer.dumps(self._wrap_payload(obj))
        return self._serializer.dumps(obj)

    def _deserialize(self, data: bytes) -> Any:
        obj = self._serializer.loads(data)
        if self._wrap_entries:
            return self._unwrap_payload(obj)
        return obj

    def _resolve_serializer(
        self, serializer: str | Serializer | None
    ) -> tuple[Serializer, bool]:
        if serializer is None:
            serializer = "pickle"

        if isinstance(serializer, str):
            name = serializer.lower()
            if name not in _BUILTIN_SERIALIZERS:
                raise ValueError(
                    "Unsupported serializer. Use 'pickle', 'json', or provide an object with dumps/loads."
                )
            serializer_obj = _BUILTIN_SERIALIZERS[name]
            return (
                serializer_obj,
                not bool(getattr(serializer_obj, "handles_entries", False)),
            )

        if hasattr(serializer, "dumps") and hasattr(serializer, "loads"):
            wrap = not bool(getattr(serializer, "handles_entries", False))
            return (serializer, wrap)

        raise TypeError("serializer must be a string or provide dumps/loads methods")

    def _make_key(self, key: str) -> str:
        """Add prefix to key."""
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Any | None:
        """Get value by key."""
        try:
            data = self.client.get(self._make_key(key))
            if data is None:
                return None
            value = self._deserialize(data)
            if isinstance(value, CacheEntry):
                return value.value if value.is_fresh() else None
            return value
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Set value with optional TTL in seconds."""
        try:
            data = self._serialize(value)
            if ttl > 0:
                expires = max(1, int(math.ceil(ttl)))
                self.client.setex(self._make_key(key), expires, data)
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
            entry = self.get_entry(key)
            if entry is None:
                return False
            return entry.is_fresh()
        except Exception:
            return False

    def get_entry(self, key: str) -> CacheEntry | None:
        """Get raw entry without enforcing freshness (used by SWR)."""
        try:
            data = self.client.get(self._make_key(key))
            if data is None:
                return None
            value = self._deserialize(data)
            if isinstance(value, CacheEntry):
                return value
            # Legacy plain values: wrap to allow SWR-style access
            now = time.time()
            return CacheEntry(value=value, fresh_until=float("inf"), created_at=now)
        except Exception:
            return None

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        """Store CacheEntry, optionally with explicit TTL."""
        try:
            data = self._serialize(entry)
            expires = None
            if ttl is not None and ttl > 0:
                expires = max(1, int(math.ceil(ttl)))
            if expires:
                self.client.setex(self._make_key(key), expires, data)
            else:
                self.client.set(self._make_key(key), data)
        except Exception as e:
            raise RuntimeError(f"Redis set_entry failed: {e}")

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """Atomic set if not exists."""
        try:
            data = self._serialize(value)
            expires = None
            if ttl > 0:
                expires = max(1, int(math.ceil(ttl)))
            result = self.client.set(self._make_key(key), data, ex=expires, nx=True)
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
        l2_ttl: int | None = None,
    ):
        """
        Initialize hybrid cache.

        Args:
            l1_cache: L1 cache (memory), defaults to InMemCache
            l2_cache: L2 cache (distributed), required
            l1_ttl: TTL for L1 cache in seconds
            l2_ttl: TTL for L2 cache in seconds, defaults to l1_ttl * 2
        """
        self.l1 = l1_cache if l1_cache is not None else InMemCache()
        if l2_cache is None:
            raise ValueError("l2_cache is required for HybridCache")
        self.l2 = l2_cache
        self.l1_ttl = l1_ttl
        self.l2_ttl = l2_ttl if l2_ttl is not None else l1_ttl * 2

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
        l2_ttl = min(ttl, self.l2_ttl) if ttl > 0 else self.l2_ttl
        self.l2.set(key, value, l2_ttl)

    def get_entry(self, key: str) -> CacheEntry | None:
        """Get raw entry preferring L1, falling back to L2 and repopulating L1."""
        entry: CacheEntry | None = None

        if hasattr(self.l1, "get_entry"):
            entry = self.l1.get_entry(key)  # type: ignore[attr-defined]
        if entry is not None:
            return entry

        # Attempt L2 entry retrieval first
        if hasattr(self.l2, "get_entry"):
            entry = self.l2.get_entry(key)  # type: ignore[attr-defined]
            if entry is not None:
                # Populate L1 with limited TTL to avoid stale accumulation
                self.l1.set_entry(key, entry, ttl=self.l1_ttl)
                return entry

        # Fall back to plain value fetch
        value = self.l2.get(key)
        if value is None:
            return None

        now = time.time()
        entry = CacheEntry(
            value=value,
            fresh_until=now + self.l1_ttl if self.l1_ttl > 0 else float("inf"),
            created_at=now,
        )
        self.l1.set_entry(key, entry, ttl=self.l1_ttl)
        return entry

    def delete(self, key: str) -> None:
        """Delete from both caches."""
        self.l1.delete(key)
        self.l2.delete(key)

    def exists(self, key: str) -> bool:
        """Check if key exists in either cache."""
        return self.l1.exists(key) or self.l2.exists(key)

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """Atomic set if not exists (L2 only for consistency)."""
        l2_ttl = min(ttl, self.l2_ttl) if ttl > 0 else self.l2_ttl
        success = self.l2.set_if_not_exists(key, value, l2_ttl)
        if success:
            self.l1.set(key, value, min(ttl, self.l1_ttl) if ttl > 0 else self.l1_ttl)
        return success

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        """Store raw entry in both layers, respecting L1 and L2 TTL."""
        ttl = ttl if ttl is not None else max(int(entry.fresh_until - time.time()), 0)

        l1_ttl = min(ttl, self.l1_ttl) if ttl > 0 else self.l1_ttl
        l2_ttl = min(ttl, self.l2_ttl) if ttl > 0 else self.l2_ttl

        self.l1.set_entry(key, entry, ttl=l1_ttl)

        if hasattr(self.l2, "set_entry"):
            self.l2.set_entry(key, entry, ttl=l2_ttl)  # type: ignore[attr-defined]
        else:
            self.l2.set(key, entry.value, l2_ttl)
