"""
OpenTelemetry metrics exporter for advanced_caching.
"""

from __future__ import annotations

from typing import Any

try:
    from opentelemetry import metrics
    from opentelemetry.metrics import Meter
except ImportError as e:
    raise ImportError(
        "opentelemetry-api is required for OpenTelemetryMetrics. "
        "Install it with: pip install 'advanced-caching[opentelemetry]'"
    ) from e


class OpenTelemetryMetrics:
    """
    OpenTelemetry metrics collector for cache operations.

    Provides the following metrics:
    - cache.hits: Counter for cache hits
    - cache.misses: Counter for cache misses
    - cache.sets: Counter for cache set operations
    - cache.deletes: Counter for cache delete operations
    - cache.errors: Counter for cache errors
    - cache.operation.duration: Histogram for operation latency
    - cache.background_refresh: Counter for background refresh operations
    - cache.memory.bytes: UpDownCounter for in-memory cache size
    - cache.entry.count: UpDownCounter for number of entries
    """

    __slots__ = (
        "_meter",
        "_hits",
        "_misses",
        "_sets",
        "_deletes",
        "_errors",
        "_latency",
        "_refresh",
        "_memory_bytes",
        "_entry_count",
    )

    def __init__(
        self,
        meter: Meter | None = None,
        meter_name: str = "advanced_caching.cache",
        meter_version: str = "1.0.0",
    ):
        """
        Initialize OpenTelemetry metrics collector.

        Args:
            meter: Optional OpenTelemetry Meter instance. If not provided, creates one.
            meter_name: Name for the meter (default: "advanced_caching.cache")
            meter_version: Version for the meter (default: "1.0.0")
        """
        if meter is None:
            meter = metrics.get_meter(meter_name, meter_version)

        self._meter = meter

        # Counters
        self._hits = self._meter.create_counter(
            name="cache.hits",
            description="Total number of cache hits",
            unit="1",
        )

        self._misses = self._meter.create_counter(
            name="cache.misses",
            description="Total number of cache misses",
            unit="1",
        )

        self._sets = self._meter.create_counter(
            name="cache.sets",
            description="Total number of cache set operations",
            unit="1",
        )

        self._deletes = self._meter.create_counter(
            name="cache.deletes",
            description="Total number of cache delete operations",
            unit="1",
        )

        self._errors = self._meter.create_counter(
            name="cache.errors",
            description="Total number of cache errors",
            unit="1",
        )

        # Histogram for latency
        self._latency = self._meter.create_histogram(
            name="cache.operation.duration",
            description="Cache operation duration in seconds",
            unit="s",
        )

        # Background refresh counter
        self._refresh = self._meter.create_counter(
            name="cache.background_refresh",
            description="Total number of background refresh operations",
            unit="1",
        )

        # UpDownCounters for memory usage (can go up and down)
        self._memory_bytes = self._meter.create_up_down_counter(
            name="cache.memory.bytes",
            description="Current memory usage in bytes",
            unit="By",
        )

        self._entry_count = self._meter.create_up_down_counter(
            name="cache.entry.count",
            description="Current number of entries in cache",
            unit="1",
        )

    def record_hit(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._hits.add(1, {"cache_name": cache_name, "decorator": decorator})

    def record_miss(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._misses.add(1, {"cache_name": cache_name, "decorator": decorator})

    def record_set(
        self,
        cache_name: str,
        key: str | None = None,
        value_size: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        attributes = {"cache_name": cache_name, "decorator": decorator}
        if value_size is not None:
            attributes["value_size_bytes"] = str(value_size)
        self._sets.add(1, attributes)

    def record_delete(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._deletes.add(1, {"cache_name": cache_name, "decorator": decorator})

    def record_latency(
        self,
        cache_name: str,
        operation: str,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._latency.record(
            duration_seconds,
            {
                "cache_name": cache_name,
                "decorator": decorator,
                "operation": operation,
            },
        )

    def record_error(
        self,
        cache_name: str,
        operation: str,
        error_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._errors.add(
            1,
            {
                "cache_name": cache_name,
                "decorator": decorator,
                "operation": operation,
                "error_type": error_type,
            },
        )

    def record_memory_usage(
        self,
        cache_name: str,
        bytes_used: int,
        entry_count: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        attributes = {"cache_name": cache_name, "decorator": decorator}

        # Note: UpDownCounter doesn't have a .set() method, we need to track deltas
        # For simplicity, we record the current value. Production use may require
        # tracking previous values to compute deltas.
        self._memory_bytes.add(bytes_used, attributes)

        if entry_count is not None:
            self._entry_count.add(entry_count, attributes)

    def record_background_refresh(
        self,
        cache_name: str,
        success: bool,
        duration_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        status = "success" if success else "failure"
        self._refresh.add(
            1,
            {
                "cache_name": cache_name,
                "decorator": decorator,
                "status": status,
            },
        )

        if duration_seconds is not None:
            # Also record refresh latency
            self._latency.record(
                duration_seconds,
                {
                    "cache_name": cache_name,
                    "decorator": decorator,
                    "operation": "refresh",
                },
            )
