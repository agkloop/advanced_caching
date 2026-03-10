from __future__ import annotations

import math
import time
from typing import Any

from .utils import CacheEntry, CacheStorage
from ..serializers import (
    Serializer,
    pack_entry,
    unpack_entry,
    resolve as _resolve_serializer,
)

try:
    import redis
except ImportError:  # pragma: no cover - optional
    redis = None  # type: ignore


class RedisCache(CacheStorage):
    """Redis-backed cache storage.

    Pass any :class:`~advanced_caching.serializers.Serializer` instance, including
    ``serializers.json``, ``serializers.msgpack``, or
    ``serializers.protobuf(MyMessage)``.  Defaults to pickle.

    Example::

        from advanced_caching import serializers, RedisCache
        import redis

        store = RedisCache(
            redis.from_url("redis://localhost"),
            prefix="myapp:",
            serializer=serializers.json,
        )
    """

    def __init__(
        self,
        redis_client: Any,
        prefix: str = "",
        serializer: Serializer | None = None,
        dedupe_writes: bool = False,
    ):
        if redis is None:
            raise ImportError("redis package required. Install: pip install redis")
        self.client = redis_client
        self.prefix = prefix
        self._ser = _resolve_serializer(serializer)
        self._dedupe_writes = dedupe_writes

    def _make_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Any | None:
        try:
            data = self.client.get(self._make_key(key))
            if data is None:
                return None
            entry = unpack_entry(data, self._ser)
            return entry.value if entry.is_fresh() else None
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int | float = 0) -> None:
        try:
            now = time.time()
            fresh_until = now + ttl if ttl > 0 else float("inf")
            entry = CacheEntry(value=value, fresh_until=fresh_until, created_at=now)
            data = pack_entry(entry, self._ser)
            if self._dedupe_writes:
                existing = self.client.get(self._make_key(key))
                if existing is not None and existing == data:
                    if ttl > 0:
                        self.client.expire(self._make_key(key), max(1, math.ceil(ttl)))
                    return
            if ttl > 0:
                self.client.setex(self._make_key(key), max(1, math.ceil(ttl)), data)
            else:
                self.client.set(self._make_key(key), data)
        except Exception as e:
            raise RuntimeError(f"Redis set failed: {e}")

    def delete(self, key: str) -> None:
        try:
            self.client.delete(self._make_key(key))
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        try:
            entry = self.get_entry(key)
            return entry is not None and entry.is_fresh()
        except Exception:
            return False

    def get_entry(self, key: str, now: float | None = None) -> CacheEntry | None:
        try:
            data = self.client.get(self._make_key(key))
            if data is None:
                return None
            return unpack_entry(data, self._ser)
        except Exception:
            return None

    def set_entry(
        self, key: str, entry: CacheEntry, ttl: int | float | None = None
    ) -> None:
        try:
            if ttl is not None:
                now = time.time()
                entry = CacheEntry(
                    value=entry.value,
                    fresh_until=now + ttl if ttl > 0 else float("inf"),
                    created_at=now,
                )
            data = pack_entry(entry, self._ser)
            if self._dedupe_writes:
                existing = self.client.get(self._make_key(key))
                if existing is not None and existing == data:
                    if ttl is not None and ttl > 0:
                        self.client.expire(self._make_key(key), max(1, math.ceil(ttl)))
                    return
            expires = max(1, math.ceil(ttl)) if ttl is not None and ttl > 0 else None
            if expires:
                self.client.setex(self._make_key(key), expires, data)
            else:
                self.client.set(self._make_key(key), data)
        except Exception as e:
            raise RuntimeError(f"Redis set_entry failed: {e}")

    def set_if_not_exists(self, key: str, value: Any, ttl: int | float) -> bool:
        try:
            now = time.time()
            fresh_until = now + ttl if ttl > 0 else float("inf")
            entry = CacheEntry(value=value, fresh_until=fresh_until, created_at=now)
            data = pack_entry(entry, self._ser)
            expires = max(1, math.ceil(ttl)) if ttl > 0 else None
            result = self.client.set(self._make_key(key), data, ex=expires, nx=True)
            return bool(result)
        except Exception:
            return False

    def clear(self) -> None:
        """Delete all keys under this cache's prefix (or flushdb if no prefix)."""
        try:
            if self.prefix:
                keys = self.client.keys(f"{self.prefix}*")
                if keys:
                    self.client.delete(*keys)
            else:
                self.client.flushdb()
        except Exception:
            pass
