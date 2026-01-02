"""
High-performance metrics collection for cache operations.

This module provides an optional, zero-overhead metrics system that tracks:
- Cache operations (hits, misses, sets, deletes)
- Latency percentiles (p50, p95, p99)
- Error rates and types
- Memory usage (for in-memory caches)
- Background refresh metrics
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any, Protocol


class MetricsCollector(Protocol):
    """
    Protocol for cache metrics collectors.

    All methods should be lightweight (< 1µs) to avoid impacting cache performance.
    Implementations should use lock-free counters or thread-local storage.
    """

    def record_hit(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record a cache hit.

        Args:
            cache_name: Identifier for the cache (e.g., decorator name or function name)
            key: Optional cache key (useful for tracking hot keys)
            metadata: Optional additional context (e.g., {'decorator': 'TTLCache'})
        """
        ...

    def record_miss(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record a cache miss.

        Args:
            cache_name: Identifier for the cache
            key: Optional cache key
            metadata: Optional additional context
        """
        ...

    def record_set(
        self,
        cache_name: str,
        key: str | None = None,
        value_size: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record a cache set operation.

        Args:
            cache_name: Identifier for the cache
            key: Optional cache key
            value_size: Optional size of the cached value in bytes
            metadata: Optional additional context
        """
        ...

    def record_delete(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record a cache delete operation.

        Args:
            cache_name: Identifier for the cache
            key: Optional cache key
            metadata: Optional additional context
        """
        ...

    def record_latency(
        self,
        cache_name: str,
        operation: str,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record operation latency.

        Args:
            cache_name: Identifier for the cache
            operation: Operation type ('get', 'set', 'delete', 'refresh')
            duration_seconds: Operation duration in seconds
            metadata: Optional additional context
        """
        ...

    def record_error(
        self,
        cache_name: str,
        operation: str,
        error_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record a cache error.

        Args:
            cache_name: Identifier for the cache
            operation: Operation that failed ('get', 'set', 'delete', 'refresh')
            error_type: Type of error (exception class name or error category)
            metadata: Optional additional context
        """
        ...

    def record_memory_usage(
        self,
        cache_name: str,
        bytes_used: int,
        entry_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record memory usage for in-memory caches.

        Args:
            cache_name: Identifier for the cache
            bytes_used: Current memory usage in bytes
            entry_count: Optional number of entries in cache
            metadata: Optional additional context
        """
        ...

    def record_background_refresh(
        self,
        cache_name: str,
        success: bool,
        duration_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Record a background refresh operation (SWRCache, BGCache).

        Args:
            cache_name: Identifier for the cache
            success: Whether the refresh succeeded
            duration_seconds: Optional refresh duration
            metadata: Optional additional context
        """
        ...


class NullMetrics:
    """
    No-op metrics collector with zero overhead.

    This is the default implementation used when metrics are not configured.
    All methods are optimized away by the Python interpreter.
    """

    __slots__ = ()

    def record_hit(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def record_miss(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def record_set(
        self,
        cache_name: str,
        key: str | None = None,
        value_size: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def record_delete(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def record_latency(
        self,
        cache_name: str,
        operation: str,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def record_error(
        self,
        cache_name: str,
        operation: str,
        error_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def record_memory_usage(
        self,
        cache_name: str,
        bytes_used: int,
        entry_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass

    def record_background_refresh(
        self,
        cache_name: str,
        success: bool,
        duration_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass


# Singleton instance for zero overhead
NULL_METRICS = NullMetrics()


class MetricsTimer:
    """
    Context manager for timing cache operations with minimal overhead.

    Usage:
        with MetricsTimer(metrics, 'my_cache', 'get'):
            result = cache.get(key)
    """

    __slots__ = ("metrics", "cache_name", "operation", "metadata", "start_time")

    def __init__(
        self,
        metrics: MetricsCollector,
        cache_name: str,
        operation: str,
        metadata: dict[str, Any] | None = None,
    ):
        self.metrics = metrics
        self.cache_name = cache_name
        self.operation = operation
        self.metadata = metadata
        self.start_time = 0.0

    def __enter__(self) -> MetricsTimer:
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        duration = time.perf_counter() - self.start_time
        self.metrics.record_latency(
            self.cache_name, self.operation, duration, self.metadata
        )
        if exc_type is not None:
            self.metrics.record_error(
                self.cache_name,
                self.operation,
                exc_type.__name__,
                self.metadata,
            )


class InMemoryMetrics:
    """
    Simple in-memory metrics collector for API queries.

    Collects metrics in memory for retrieval via API endpoints.
    Useful for debugging and simple monitoring without external dependencies.

    Thread-safe and lightweight. Stores aggregated counters and recent latencies.

    Example:
        from advanced_caching import TTLCache
        from advanced_caching.metrics import InMemoryMetrics

        # Create metrics collector
        metrics = InMemoryMetrics()

        # Use with cache
        @TTLCache.cached("user:{id}", ttl=60, metrics=metrics)
        def get_user(id: int):
            return {"id": id, "name": "Alice"}

        # Query metrics via API
        @app.get("/metrics")
        def get_metrics():
            return metrics.get_stats()
    """

    __slots__ = (
        "_lock",
        "_hits",
        "_misses",
        "_sets",
        "_deletes",
        "_errors",
        "_latencies",
        "_max_samples",
        "_memory",
        "_refreshes",
        "_start_time",
    )

    def __init__(self, max_latency_samples: int = 1000):
        """
        Initialize in-memory metrics collector.

        Args:
            max_latency_samples: Maximum number of latency samples to keep per operation
        """
        self._lock = threading.Lock()
        self._hits: dict[str, int] = defaultdict(int)
        self._misses: dict[str, int] = defaultdict(int)
        self._sets: dict[str, int] = defaultdict(int)
        self._deletes: dict[str, int] = defaultdict(int)
        self._errors: dict[tuple[str, str, str], int] = defaultdict(int)

        # Store recent latencies for percentile calculation
        self._latencies: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._max_samples = max_latency_samples

        # Memory usage (latest value per cache)
        self._memory: dict[str, dict[str, Any]] = {}

        # Background refresh stats
        self._refreshes: dict[tuple[str, bool], int] = defaultdict(int)

        self._start_time = time.time()

    def record_hit(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._hits[cache_name] += 1

    def record_miss(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._misses[cache_name] += 1

    def record_set(
        self,
        cache_name: str,
        key: str | None = None,
        value_size: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._sets[cache_name] += 1

    def record_delete(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._deletes[cache_name] += 1

    def record_latency(
        self,
        cache_name: str,
        operation: str,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = (cache_name, operation)
        with self._lock:
            samples = self._latencies[key]
            samples.append(duration_seconds)

            # Keep only recent samples
            if len(samples) > self._max_samples:
                samples.pop(0)

    def record_error(
        self,
        cache_name: str,
        operation: str,
        error_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = (cache_name, operation, error_type)
        with self._lock:
            self._errors[key] += 1

    def record_memory_usage(
        self,
        cache_name: str,
        bytes_used: int,
        entry_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._memory[cache_name] = {
                "bytes": bytes_used,
                "entries": entry_count,
                "mb": bytes_used / (1024 * 1024),
            }

    def record_background_refresh(
        self,
        cache_name: str,
        success: bool,
        duration_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        key = (cache_name, success)
        with self._lock:
            self._refreshes[key] += 1

    def get_stats(self) -> dict[str, Any]:
        """
        Get all collected metrics as a dictionary.

        Returns:
            Dict containing all metrics, suitable for JSON serialization.
        """
        with self._lock:
            # Calculate hit rates
            cache_stats = {}
            all_caches = set(self._hits.keys()) | set(self._misses.keys())

            for cache_name in all_caches:
                hits = self._hits[cache_name]
                misses = self._misses[cache_name]
                total = hits + misses
                hit_rate = (hits / total * 100) if total > 0 else 0.0

                cache_stats[cache_name] = {
                    "hits": hits,
                    "misses": misses,
                    "sets": self._sets[cache_name],
                    "deletes": self._deletes[cache_name],
                    "hit_rate_percent": round(hit_rate, 2),
                }

            # Calculate latency percentiles
            latency_stats = {}
            for (cache_name, operation), samples in self._latencies.items():
                if samples:
                    sorted_samples = sorted(samples)
                    n = len(sorted_samples)
                    latency_stats[f"{cache_name}.{operation}"] = {
                        "count": n,
                        "p50_ms": round(sorted_samples[n // 2] * 1000, 3),
                        "p95_ms": round(sorted_samples[int(n * 0.95)] * 1000, 3),
                        "p99_ms": round(sorted_samples[int(n * 0.99)] * 1000, 3),
                        "avg_ms": round(sum(samples) / n * 1000, 3),
                    }

            # Format errors
            error_stats = {}
            for (cache_name, operation, error_type), count in self._errors.items():
                key = f"{cache_name}.{operation}"
                if key not in error_stats:
                    error_stats[key] = {}
                error_stats[key][error_type] = count

            # Background refresh stats
            refresh_stats = {}
            for (cache_name, success), count in self._refreshes.items():
                if cache_name not in refresh_stats:
                    refresh_stats[cache_name] = {"success": 0, "failure": 0}
                refresh_stats[cache_name]["success" if success else "failure"] = count

            return {
                "uptime_seconds": round(time.time() - self._start_time, 1),
                "caches": cache_stats,
                "latency": latency_stats,
                "errors": error_stats,
                "memory": dict(self._memory),
                "background_refresh": refresh_stats,
            }

    def reset(self) -> None:
        """Reset all metrics to zero."""
        with self._lock:
            self._hits.clear()
            self._misses.clear()
            self._sets.clear()
            self._deletes.clear()
            self._errors.clear()
            self._latencies.clear()
            self._memory.clear()
            self._refreshes.clear()
            self._start_time = time.time()
