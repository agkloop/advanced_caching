# Metrics Collection

Optional metrics system with <1% overhead. Tracks hits, misses, latency, errors, and background refreshes.

## Installation

```bash

uv pip install "advanced-caching"  # Includes InMemoryMetrics
pip install "advanced-caching[opentelemetry]"  # OpenTelemetry
uv pip install "advanced-caching[gcp-monitoring]"  # GCP Cloud Monitoring
```

## Quick Start

```python
from advanced_caching import TTLCache
from advanced_caching.metrics import InMemoryMetrics

metrics = InMemoryMetrics()  # Share across multiple functions

@TTLCache.cached("user:{id}", ttl=60, metrics=metrics)
def get_user(id: int):
    return {"id": id}

# Query stats
stats = metrics.get_stats()
# Returns: hits, misses, hit_rate, latency percentiles, errors, memory, background_refresh
```

## Metrics Reference

All metrics collectors track the following operations and expose them through their respective backends.

| Metric Name | Type | What It Represents | When Recorded | Use Case | Labels/Dimensions |
|-------------|------|-------------------|---------------|----------|-------------------|
| **`cache.hits`** | Counter | Number of times data was successfully retrieved from cache without executing the underlying function | Every time a cache lookup finds valid (non-expired) data | Calculate cache effectiveness. High hit count indicates good cache utilization | `cache_name`, `operation` (always "get") |
| **`cache.misses`** | Counter | Number of times data was not found in cache or was expired, requiring function execution | When cache lookup fails (key not found or TTL expired) | Identify cold cache scenarios or TTL tuning needs. High miss rate may indicate TTL is too short | `cache_name`, `operation` (always "get") |
| **`cache.sets`** | Counter | Number of times data was written to cache after function execution | After the underlying function completes successfully and result is stored | Track cache write operations. Should roughly equal misses in normal operation | `cache_name`, `operation` (always "set") |
| **`cache.deletes`** | Counter | Number of explicit cache entry removals (not TTL expirations) | When cache entries are manually deleted or evicted by cache policy | Monitor cache invalidation patterns. Debug cache coherency issues | `cache_name`, `operation` (always "delete") |
| **`cache.hit_rate_percent`** | Gauge (Calculated) | Percentage of cache lookups that resulted in hits: `(hits / (hits + misses)) * 100` | Calculated on-demand (InMemoryMetrics) or periodically (exporters) | **Primary effectiveness metric.** Target: >80% for most apps, >95% for read-heavy workloads. Values: `95.5` = 95.5% from cache, `50.0` = half hit/miss, `0.0` = cold cache | `cache_name` |
| **`cache.operation.duration`** | Histogram/Timer | Time spent in cache operations (get, set, delete) in milliseconds. Provides p50, p95, p99, avg aggregations | For every cache operation, wrapping the storage backend call | Detect storage backend performance issues. Compare local vs remote cache (Redis, S3, GCS). **Example:** `get_p50_ms: 0.12` = fast in-memory, `get_p99_ms: 45.0` = 1% take up to 45ms (network spike?) | `cache_name`, `operation` (get/set/delete) |
| **`cache.errors`** | Counter | Number of errors encountered during cache operations | When cache operations raise exceptions (network failures, serialization errors, Redis connection issues) | Alert on storage backend failures. Identify problematic cache keys. Monitor Redis connection health. Breakdown by `error_type` (e.g., ConnectionError, TimeoutError) | `cache_name`, `operation`, `error_type` |
| **`cache.background_refresh`** | Counter (success/failure breakdown) | Number of background refresh operations for SWRCache (stale refresh) and BGCache (scheduled refresh) | **SWRCache:** When serving stale data triggers background refresh<br>**BGCache:** On every scheduled loader execution | Monitor SWR effectiveness (serving stale while updating). Track BGCache job reliability. High failure rate indicates unreliable data source, network issues, or function errors | `cache_name`, `status` (success/failure) |
| **`cache.memory.bytes`** | Gauge | Approximate memory usage of cached entries in bytes. Also provides `mb` (megabytes) and `entries` (item count) | Periodically or on-demand when using `InstrumentedStorage` wrapper | Prevent memory exhaustion in long-running processes. Size L1 cache appropriately in HybridCache. Trigger eviction at threshold | `cache_name` |
| **`cache.entry.count`** | Gauge | Number of entries currently stored in cache | Tracked alongside memory metrics | Monitor cache growth over time. Validate cache eviction policies. Estimate memory per entry (bytes / entries) | `cache_name` |

---

## Metric Naming Conventions

### InMemoryMetrics
Returns nested dictionary structure:
```json
{
  "uptime_seconds": 3600.5,
  "caches": {
    "get_user": {
      "hits": 100,
      "misses": 20,
      "sets": 20,
      "deletes": 5,
      "hit_rate_percent": 83.33
    },
    "get_product": {
      "hits": 50,
      "misses": 10,
      "sets": 10,
      "deletes": 2,
      "hit_rate_percent": 83.33
    }
  },
  "latency": {
    "get_user.get_p50_ms": 0.15,
    "get_user.get_p95_ms": 2.5,
    "get_user.get_p99_ms": 10.0,
    "get_user.get_avg_ms": 0.8,
    "get_product.get_p50_ms": 0.12,
    "get_product.set_p50_ms": 1.2
  },
  "errors": {
    "get_user.get": {
      "ConnectionError": 5,
      "TimeoutError": 2
    }
  },
  "memory": {
    "my_cache": {
      "bytes": 1048576,
      "mb": 1.0,
      "entries": 100
    },
    "another_cache": {
      "bytes": 524288,
      "mb": 0.5,
      "entries": 50
    }
  },
  "background_refresh": {
    "get_user": {
      "success": 50,
      "failure": 2
    }
  }
}
```

**Note:** Metrics are tracked **per-cache-name** when using `InstrumentedStorage` wrapper. If you have multiple functions sharing the same metrics collector but using different storage backends, each will have its own memory entry under the cache name you provide to `InstrumentedStorage(storage, metrics, "cache_name")`.

### OpenTelemetry
Metric names follow OpenTelemetry conventions:
- `cache.hits` (Counter with `cache_name` attribute)
- `cache.misses` (Counter with `cache_name` attribute)
- `cache.operation.duration` (Histogram with `cache_name`, `operation` attributes)

### GCP Cloud Monitoring
Uses custom metric paths under your configured prefix:
- `custom.googleapis.com/<prefix>/hits`
- `custom.googleapis.com/<prefix>/misses`
- `custom.googleapis.com/<prefix>/latency`

Labels: `cache_name`, `operation`

---

## InMemoryMetrics

Built-in collector for API endpoints. Zero external dependencies, thread-safe.

```python
from fastapi import FastAPI

app = FastAPI()
metrics = InMemoryMetrics()

@app.get("/metrics")
async def get_metrics():
    return metrics.get_stats()
```

**Configuration:**
```python
metrics = InMemoryMetrics(max_latency_samples=1000)
metrics.reset()  # Clear all stats
```

## Exporters

### OpenTelemetry

```python
from advanced_caching.exporters import OpenTelemetryMetrics
from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider

otel_metrics = OpenTelemetryMetrics(meter_name="myapp.cache")

@TTLCache.cached("user:{id}", ttl=60, metrics=otel_metrics)
def get_user(id: int):
    return {"id": id}
```

### GCP Cloud Monitoring

```python
from advanced_caching.exporters import GCPCloudMonitoringMetrics

gcp_metrics = GCPCloudMonitoringMetrics(
    project_id="my-project",
    metric_prefix="custom.googleapis.com/myapp/cache",
    flush_interval=60.0,
)

@TTLCache.cached("session:{id}", ttl=3600, metrics=gcp_metrics)
def get_session(id: str):
    return {"id": id}
```

**Share client across collectors:**
```python
from google.cloud import monitoring_v3

client = monitoring_v3.MetricServiceClient()

metrics1 = GCPCloudMonitoringMetrics(project_id="my-project", client=client)
metrics2 = GCPCloudMonitoringMetrics(project_id="my-project", client=client)
```

### Custom Exporters

See [Custom Exporters Guide](custom-metrics-exporters.md) for Prometheus, StatsD, and Datadog examples.

## Advanced Usage

### Shared Metrics Collector

**Share one collector across all cached functions** (recommended):

```python
metrics = InMemoryMetrics()

@TTLCache.cached("user:{id}", ttl=60, metrics=metrics)
def get_user(id: int):
    return {"id": id}

@TTLCache.cached("product:{id}", ttl=300, metrics=metrics)
def get_product(id: int):
    return {"id": id}

# Per-function stats in single collector
stats = metrics.get_stats()
# stats["caches"]["get_user"] → user cache metrics
# stats["caches"]["get_product"] → product cache metrics
```

### Memory Monitoring

```python
from advanced_caching.storage import InstrumentedStorage, InMemCache

cache = InstrumentedStorage(InMemCache(), metrics, "my_cache")

@TTLCache.cached("key:{id}", storage=cache, ttl=60)
def get_data(id: int):
    return {"id": id}
```

### Conditional Metrics

```python
import os
from advanced_caching.metrics import NULL_METRICS, InMemoryMetrics

metrics = InMemoryMetrics() if os.getenv("ENV") == "production" else NULL_METRICS
```

## Performance

<1% overhead for InMemoryMetrics. Use `NULL_METRICS` for zero overhead in development.

## API Reference

- [`metrics.py`](../src/advanced_caching/metrics.py) - Core metrics (InMemoryMetrics, NullMetrics)
- [`exporters/otel.py`](../src/advanced_caching/exporters/otel.py) - OpenTelemetry
- [`exporters/gcp.py`](../src/advanced_caching/exporters/gcp.py) - GCP Cloud Monitoring
- [Custom Exporters Guide](custom-metrics-exporters.md) - Prometheus, StatsD, Datadog examples