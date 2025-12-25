from __future__ import annotations

import gzip
import hashlib
import json
import math
import pickle
import time
from dataclasses import dataclass
from typing import Any, Protocol

import orjson


class Serializer(Protocol):
    """Simple serializer protocol used by cache backends."""

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


def _hash_bytes(data: bytes) -> str:
    """Cheap content hash (blake2b) used to skip redundant writes."""
    return hashlib.blake2b(data, digest_size=16).hexdigest()


@dataclass(slots=True)
class CacheEntry:
    """Internal cache entry with TTL support."""

    value: Any
    fresh_until: float  # Unix timestamp
    created_at: float

    def is_fresh(self, now: float | None = None) -> bool:
        if now is None:
            now = time.time()
        return now < self.fresh_until

    def age(self, now: float | None = None) -> float:
        if now is None:
            now = time.time()
        return now - self.created_at


class CacheStorage(Protocol):
    """Protocol for cache storage backends."""

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl: int = 0) -> None: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def get_entry(self, key: str) -> CacheEntry | None: ...

    def set_entry(
        self, key: str, entry: CacheEntry, ttl: int | None = None
    ) -> None: ...

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool: ...

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Retrieve multiple keys at once. Default implementation is sequential."""
        return {k: v for k in keys if (v := self.get(k)) is not None}

    def set_many(self, mapping: dict[str, Any], ttl: int = 0) -> None:
        """Set multiple keys at once. Default implementation is sequential."""
        for k, v in mapping.items():
            self.set(k, v, ttl)


def validate_cache_storage(cache: Any) -> bool:
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
        hasattr(cache, m) and callable(getattr(cache, m)) for m in required_methods
    )
