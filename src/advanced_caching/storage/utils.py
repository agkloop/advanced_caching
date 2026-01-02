from __future__ import annotations

import gzip
import hashlib
import json
import math
import pickle
import sys
import time
from dataclasses import dataclass
from typing import Any, Protocol, TYPE_CHECKING

import orjson

if TYPE_CHECKING:
    from ..metrics import MetricsCollector


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


class InstrumentedStorage:
    """
    Wrapper that adds metrics collection to any CacheStorage backend.

    This wrapper adds minimal overhead (<1µs per operation) by recording
    cache hits, misses, latency, and errors. It's transparent to the
    underlying storage backend.

    Example:
        from advanced_caching.metrics import NULL_METRICS
        from advanced_caching.exporters.prometheus import PrometheusMetrics

        # Without metrics (zero overhead)
        cache = InMemCache()

        # With metrics
        metrics = PrometheusMetrics()
        cache = InstrumentedStorage(InMemCache(), metrics, "my_cache")
    """

    __slots__ = ("_storage", "_metrics", "_cache_name", "_metadata")

    def __init__(
        self,
        storage: CacheStorage,
        metrics: MetricsCollector,
        cache_name: str,
        metadata: dict[str, Any] | None = None,
    ):
        """
        Args:
            storage: Underlying storage backend to instrument
            metrics: MetricsCollector instance to record metrics to
            cache_name: Identifier for this cache (used in metric labels)
            metadata: Optional metadata to include with all metrics
        """
        self._storage = storage
        self._metrics = metrics
        self._cache_name = cache_name
        self._metadata = metadata or {}

    def get(self, key: str) -> Any | None:
        start = time.perf_counter()
        try:
            result = self._storage.get(key)
            duration = time.perf_counter() - start

            if result is not None:
                self._metrics.record_hit(self._cache_name, key, self._metadata)
            else:
                self._metrics.record_miss(self._cache_name, key, self._metadata)

            self._metrics.record_latency(
                self._cache_name, "get", duration, self._metadata
            )
            return result
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "get", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "get", duration, self._metadata
            )
            raise

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        start = time.perf_counter()
        try:
            self._storage.set(key, value, ttl)
            duration = time.perf_counter() - start

            # Estimate value size
            value_size = None
            try:
                value_size = sys.getsizeof(value)
            except (TypeError, AttributeError):
                pass

            self._metrics.record_set(self._cache_name, key, value_size, self._metadata)
            self._metrics.record_latency(
                self._cache_name, "set", duration, self._metadata
            )
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "set", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "set", duration, self._metadata
            )
            raise

    def delete(self, key: str) -> None:
        start = time.perf_counter()
        try:
            self._storage.delete(key)
            duration = time.perf_counter() - start

            self._metrics.record_delete(self._cache_name, key, self._metadata)
            self._metrics.record_latency(
                self._cache_name, "delete", duration, self._metadata
            )
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "delete", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "delete", duration, self._metadata
            )
            raise

    def exists(self, key: str) -> bool:
        # exists() is typically a wrapper around get(), so we don't
        # record separate metrics to avoid double-counting
        return self._storage.exists(key)

    def get_entry(self, key: str) -> CacheEntry | None:
        start = time.perf_counter()
        try:
            result = self._storage.get_entry(key)
            duration = time.perf_counter() - start

            if result is not None:
                self._metrics.record_hit(self._cache_name, key, self._metadata)
            else:
                self._metrics.record_miss(self._cache_name, key, self._metadata)

            self._metrics.record_latency(
                self._cache_name, "get_entry", duration, self._metadata
            )
            return result
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "get_entry", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "get_entry", duration, self._metadata
            )
            raise

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        start = time.perf_counter()
        try:
            self._storage.set_entry(key, entry, ttl)
            duration = time.perf_counter() - start

            # Estimate entry size
            value_size = None
            try:
                value_size = sys.getsizeof(entry.value)
            except (TypeError, AttributeError):
                pass

            self._metrics.record_set(self._cache_name, key, value_size, self._metadata)
            self._metrics.record_latency(
                self._cache_name, "set_entry", duration, self._metadata
            )
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "set_entry", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "set_entry", duration, self._metadata
            )
            raise

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        start = time.perf_counter()
        try:
            result = self._storage.set_if_not_exists(key, value, ttl)
            duration = time.perf_counter() - start

            if result:
                # Estimate value size
                value_size = None
                try:
                    value_size = sys.getsizeof(value)
                except (TypeError, AttributeError):
                    pass

                self._metrics.record_set(
                    self._cache_name, key, value_size, self._metadata
                )

            self._metrics.record_latency(
                self._cache_name, "set_if_not_exists", duration, self._metadata
            )
            return result
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "set_if_not_exists", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "set_if_not_exists", duration, self._metadata
            )
            raise

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        start = time.perf_counter()
        try:
            result = self._storage.get_many(keys)
            duration = time.perf_counter() - start

            # Record hits and misses
            for key in keys:
                if key in result:
                    self._metrics.record_hit(self._cache_name, key, self._metadata)
                else:
                    self._metrics.record_miss(self._cache_name, key, self._metadata)

            self._metrics.record_latency(
                self._cache_name, "get_many", duration, self._metadata
            )
            return result
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "get_many", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "get_many", duration, self._metadata
            )
            raise

    def set_many(self, mapping: dict[str, Any], ttl: int = 0) -> None:
        start = time.perf_counter()
        try:
            self._storage.set_many(mapping, ttl)
            duration = time.perf_counter() - start

            # Record each set
            for key, value in mapping.items():
                value_size = None
                try:
                    value_size = sys.getsizeof(value)
                except (TypeError, AttributeError):
                    pass

                self._metrics.record_set(
                    self._cache_name, key, value_size, self._metadata
                )

            self._metrics.record_latency(
                self._cache_name, "set_many", duration, self._metadata
            )
        except Exception as e:
            duration = time.perf_counter() - start
            self._metrics.record_error(
                self._cache_name, "set_many", type(e).__name__, self._metadata
            )
            self._metrics.record_latency(
                self._cache_name, "set_many", duration, self._metadata
            )
            raise

    def get_memory_usage(self) -> dict[str, Any]:
        """
        Get memory usage if the underlying storage supports it.

        Returns empty dict if not supported.
        """
        if hasattr(self._storage, "get_memory_usage"):
            usage = self._storage.get_memory_usage()
            # Report to metrics
            self._metrics.record_memory_usage(
                self._cache_name,
                usage.get("bytes_used", 0),
                usage.get("entry_count"),
                self._metadata,
            )
            return usage
        return {}

    @property
    def unwrapped_storage(self) -> CacheStorage:
        """Access the underlying storage backend directly."""
        return self._storage
