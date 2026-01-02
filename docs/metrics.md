# Metrics Collection

Optional metrics system with <1% overhead. Tracks hits, misses, latency, errors, and background refreshes.

## Installation

```bash
pip install advanced-caching  # Includes InMemoryMetrics
pip install "advanced-caching[opentelemetry]"  # OpenTelemetry
pip install "advanced-caching[gcp-monitoring]"  # GCP Cloud Monitoring
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