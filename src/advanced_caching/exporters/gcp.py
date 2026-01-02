"""
Google Cloud Monitoring metrics exporter for advanced_caching.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any

try:
    from google.cloud import monitoring_v3
    from google.api import label_pb2
    from google.api import metric_pb2
except ImportError as e:
    raise ImportError(
        "google-cloud-monitoring is required for GCPCloudMonitoringMetrics. "
        "Install it with: pip install 'advanced-caching[gcp-monitoring]'"
    ) from e

from .._schedulers import SharedScheduler


class GCPCloudMonitoringMetrics:
    """
    Google Cloud Monitoring metrics collector for cache operations.

    Sends metrics to GCP Cloud Monitoring with automatic batching and
    background flushing to minimize performance impact.

    Provides the following metrics:
    - cache/hits: INT64 cumulative metric for cache hits
    - cache/misses: INT64 cumulative metric for cache misses
    - cache/sets: INT64 cumulative metric for cache set operations
    - cache/deletes: INT64 cumulative metric for cache delete operations
    - cache/errors: INT64 cumulative metric for cache errors
    - cache/operation_latency: DOUBLE distribution metric for operation latency
    - cache/background_refresh: INT64 cumulative metric for background refreshes
    - cache/memory_bytes: INT64 gauge metric for in-memory cache size
    - cache/entry_count: INT64 gauge metric for number of entries
    """

    __slots__ = (
        "_project_id",
        "_metric_prefix",
        "_client",
        "_owns_client",
        "_project_name",
        "_counters",
        "_gauges",
        "_lock",
        "_flush_interval",
        "_job_id",
        "_running",
    )

    def __init__(
        self,
        project_id: str,
        metric_prefix: str = "custom.googleapis.com/advanced_caching",
        flush_interval: float = 60.0,
        credentials: Any | None = None,
        client: monitoring_v3.MetricServiceClient | None = None,
    ):
        """
        Initialize GCP Cloud Monitoring metrics collector.

        Args:
            project_id: GCP project ID
            metric_prefix: Prefix for all metric names (default: "custom.googleapis.com/advanced_caching")
            flush_interval: How often to flush metrics to GCP (seconds, default: 60)
            credentials: Optional GCP credentials (default: uses application default credentials)
            client: Optional MetricServiceClient instance. If provided, credentials is ignored.
                   Useful for sharing a client across multiple metrics collectors.
        """
        self._project_id = project_id
        self._metric_prefix = metric_prefix.rstrip("/")

        # Use provided client or create new one
        if client is not None:
            self._client = client
            self._owns_client = False
        else:
            self._client = monitoring_v3.MetricServiceClient(credentials=credentials)
            self._owns_client = True

        self._project_name = f"projects/{project_id}"

        # Buffered counters and gauges
        self._counters: dict[tuple[str, tuple], int] = defaultdict(int)
        self._gauges: dict[tuple[str, tuple], float] = {}
        self._lock = threading.Lock()

        # Background flushing using shared scheduler (no dedicated thread)
        self._flush_interval = flush_interval
        self._running = True

        # Schedule periodic flush using shared APScheduler
        scheduler = SharedScheduler.get_scheduler()
        self._job_id = f"gcp_metrics_flush_{id(self)}"
        scheduler.add_job(
            self._safe_flush,
            "interval",
            seconds=flush_interval,
            id=self._job_id,
            replace_existing=True,
        )
        SharedScheduler.start()

    def _safe_flush(self) -> None:
        """Flush metrics, suppressing errors to avoid crashing the scheduler."""
        if not self._running:
            return
        try:
            self.flush()
        except Exception:
            # Suppress errors to avoid crashing the scheduler
            pass

    def flush(self) -> None:
        """
        Flush all buffered metrics to GCP Cloud Monitoring.

        Called automatically by background thread, but can also be called
        manually for immediate flushing.
        """
        with self._lock:
            if not self._counters and not self._gauges:
                return

            # Create time series
            series = []
            now = time.time()

            # Flush counters
            for (metric_name, labels_tuple), value in self._counters.items():
                labels_dict = dict(labels_tuple)
                series.append(
                    self._create_time_series(
                        metric_name, value, labels_dict, now, metric_kind="CUMULATIVE"
                    )
                )

            # Flush gauges
            for (metric_name, labels_tuple), value in self._gauges.items():
                labels_dict = dict(labels_tuple)
                series.append(
                    self._create_time_series(
                        metric_name, value, labels_dict, now, metric_kind="GAUGE"
                    )
                )

            # Send to GCP in batches of 200 (GCP limit)
            batch_size = 200
            for i in range(0, len(series), batch_size):
                batch = series[i : i + batch_size]
                self._client.create_time_series(
                    name=self._project_name,
                    time_series=batch,
                )

            # Clear counters (keep gauges for next update)
            self._counters.clear()

    def _create_time_series(
        self,
        metric_name: str,
        value: float,
        labels: dict[str, str],
        timestamp: float,
        metric_kind: str = "GAUGE",
    ) -> monitoring_v3.TimeSeries:
        """Create a GCP TimeSeries object."""
        series = monitoring_v3.TimeSeries()
        series.metric.type = f"{self._metric_prefix}/{metric_name}"

        for key, val in labels.items():
            series.metric.labels[key] = str(val)

        series.resource.type = "global"

        point = monitoring_v3.Point()
        point.value.int64_value = int(value) if metric_kind != "DISTRIBUTION" else 0
        point.value.double_value = (
            float(value) if metric_kind == "DISTRIBUTION" else 0.0
        )

        # Convert timestamp to protobuf Timestamp
        interval = monitoring_v3.TimeInterval()
        interval.end_time.seconds = int(timestamp)
        interval.end_time.nanos = int((timestamp - int(timestamp)) * 1e9)

        if metric_kind == "CUMULATIVE":
            interval.start_time.seconds = int(timestamp) - 60  # 1 minute window

        point.interval.CopyFrom(interval)
        series.points.append(point)

        return series

    def _increment_counter(self, metric_name: str, labels: dict[str, str]) -> None:
        """Increment a counter metric."""
        labels_tuple = tuple(sorted(labels.items()))
        with self._lock:
            self._counters[(metric_name, labels_tuple)] += 1

    def _set_gauge(
        self, metric_name: str, value: float, labels: dict[str, str]
    ) -> None:
        """Set a gauge metric."""
        labels_tuple = tuple(sorted(labels.items()))
        with self._lock:
            self._gauges[(metric_name, labels_tuple)] = value

    def record_hit(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._increment_counter(
            "cache/hits", {"cache_name": cache_name, "decorator": decorator}
        )

    def record_miss(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._increment_counter(
            "cache/misses", {"cache_name": cache_name, "decorator": decorator}
        )

    def record_set(
        self,
        cache_name: str,
        key: str | None = None,
        value_size: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._increment_counter(
            "cache/sets", {"cache_name": cache_name, "decorator": decorator}
        )

    def record_delete(
        self,
        cache_name: str,
        key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._increment_counter(
            "cache/deletes", {"cache_name": cache_name, "decorator": decorator}
        )

    def record_latency(
        self,
        cache_name: str,
        operation: str,
        duration_seconds: float,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # For simplicity, we track latency as a gauge (last value)
        # Production use might want to use distribution metrics
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        self._set_gauge(
            "cache/operation_latency",
            duration_seconds * 1000,  # Convert to ms
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
        self._increment_counter(
            "cache/errors",
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
        labels = {"cache_name": cache_name, "decorator": decorator}

        self._set_gauge("cache/memory_bytes", bytes_used, labels)

        if entry_count is not None:
            self._set_gauge("cache/entry_count", entry_count, labels)

    def record_background_refresh(
        self,
        cache_name: str,
        success: bool,
        duration_seconds: float | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        decorator = metadata.get("decorator", "unknown") if metadata else "unknown"
        status = "success" if success else "failure"
        self._increment_counter(
            "cache/background_refresh",
            {
                "cache_name": cache_name,
                "decorator": decorator,
                "status": status,
            },
        )

    def shutdown(self, flush_remaining: bool = True) -> None:
        """
        Shutdown the metrics collector.

        Args:
            flush_remaining: Whether to flush remaining metrics before shutdown
        """
        self._running = False

        # Remove scheduled job
        try:
            scheduler = SharedScheduler.get_scheduler()
            scheduler.remove_job(self._job_id)
        except Exception:
            pass

        if flush_remaining:
            self.flush()

    def __del__(self) -> None:
        """Cleanup on deletion."""
        try:
            self.shutdown(flush_remaining=True)
        except Exception:
            pass
