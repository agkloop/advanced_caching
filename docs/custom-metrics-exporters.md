## Creating Your Own Exporter

To create a custom exporter, implement the `MetricsCollector` protocol:

```python
from advanced_caching.metrics import MetricsCollector
from typing import Any

class MyCustomMetrics:
    """Your custom metrics implementation."""
    
    def record_hit(self, cache_name: str, key: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        # Your implementation
        pass
    
    def record_miss(self, cache_name: str, key: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        pass
    
    def record_set(self, cache_name: str, key: str | None = None, value_size: int | None = None, metadata: dict[str, Any] | None = None) -> None:
        pass
    
    def record_delete(self, cache_name: str, key: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        pass
    
    def record_latency(self, cache_name: str, operation: str, duration_seconds: float, metadata: dict[str, Any] | None = None) -> None:
        pass
    
    def record_error(self, cache_name: str, operation: str, error_type: str, metadata: dict[str, Any] | None = None) -> None:
        pass
    
    def record_memory_usage(self, cache_name: str, bytes_used: int, entry_count: int | None = None, metadata: dict[str, Any] | None = None) -> None:
        pass
    
    def record_background_refresh(self, cache_name: str, success: bool, duration_seconds: float | None = None, metadata: dict[str, Any] | None = None) -> None:
        pass
```

## Performance Tips

2. **Batch writes**: For HTTP-based exporters, batch multiple metrics into single requests
3. **Async export**: Export metrics asynchronously to avoid blocking cache operations
4. **Sample rates**: For very high traffic, consider sampling (e.g., record 1 in 10 operations)
5. **Buffer metrics**: Collect metrics in memory and flush periodically

## See Also

- [Main Metrics Documentation](metrics.md)
- [GCP Cloud Monitoring](metrics.md#gcp-cloud-monitoring)
- [OpenTelemetry](metrics.md#opentelemetry)
