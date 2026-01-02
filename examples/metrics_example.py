"""
Example demonstrating metrics collection with advanced_caching.

This example shows how to use metrics with different decorators and exporters.
"""

import asyncio
import time
from advanced_caching import TTLCache, SWRCache, BGCache
from advanced_caching.storage import InMemCache


def example_basic_metrics():
    """Example using MockMetrics for testing."""
    print("=== Example 1: Basic Metrics Collection ===\n")
    
    # Create a simple metrics collector (for demo purposes)
    class SimpleMetrics:
        def __init__(self):
            self.hits = 0
            self.misses = 0
            self.sets = 0
        
        def record_hit(self, cache_name, key=None, metadata=None):
            self.hits += 1
            print(f"✓ Cache HIT for {cache_name}")
        
        def record_miss(self, cache_name, key=None, metadata=None):
            self.misses += 1
            print(f"✗ Cache MISS for {cache_name}")
        
        def record_set(self, cache_name, key=None, value_size=None, metadata=None):
            self.sets += 1
            print(f"→ Cache SET for {cache_name}")
        
        def record_delete(self, cache_name, key=None, metadata=None):
            pass
        
        def record_latency(self, cache_name, operation, duration_seconds, metadata=None):
            print(f"⏱ {cache_name}.{operation} took {duration_seconds*1000:.2f}ms")
        
        def record_error(self, cache_name, operation, error_type, metadata=None):
            print(f"⚠ {cache_name}.{operation} error: {error_type}")
        
        def record_memory_usage(self, cache_name, bytes_used, entry_count=None, metadata=None):
            print(f"💾 {cache_name} using {bytes_used} bytes ({entry_count} entries)")
        
        def record_background_refresh(self, cache_name, success, duration_seconds=None, metadata=None):
            status = "✓" if success else "✗"
            print(f"{status} Background refresh for {cache_name}")
    
    metrics = SimpleMetrics()
    
    # Use metrics with TTLCache
    @TTLCache.cached("user:{}", ttl=60, metrics=metrics)
    def get_user(user_id: int):
        time.sleep(0.1)  # Simulate DB query
        return {"id": user_id, "name": f"User{user_id}"}
    
    print("First call (cold cache):")
    result = get_user(123)
    print(f"Result: {result}\n")
    
    print("Second call (warm cache):")
    result = get_user(123)
    print(f"Result: {result}\n")
    
    print(f"Total stats: {metrics.hits} hits, {metrics.misses} misses, {metrics.sets} sets\n")


def example_memory_tracking():
    """Example tracking memory usage of in-memory cache."""
    print("=== Example 2: Memory Usage Tracking ===\n")
    
    from advanced_caching.storage import InstrumentedStorage
    
    class MemoryTracker:
        def record_hit(self, *args, **kwargs):
            pass
        def record_miss(self, *args, **kwargs):
            pass
        def record_set(self, *args, **kwargs):
            pass
        def record_delete(self, *args, **kwargs):
            pass
        def record_latency(self, *args, **kwargs):
            pass
        def record_error(self, *args, **kwargs):
            pass
        def record_background_refresh(self, *args, **kwargs):
            pass
        
        def record_memory_usage(self, cache_name, bytes_used, entry_count=None, metadata=None):
            mb = bytes_used / (1024 * 1024)
            print(f"💾 Cache '{cache_name}': {mb:.2f} MB ({entry_count} entries)")
    
    tracker = MemoryTracker()
    cache = InMemCache()
    instrumented = InstrumentedStorage(cache, tracker, "my_cache")
    
    # Add some data
    for i in range(100):
        instrumented.set(f"key_{i}", "x" * 10000, ttl=60)
    
    # Check memory usage
    usage = instrumented.get_memory_usage()
    print(f"Average entry size: {usage['avg_entry_size']} bytes\n")


async def example_prometheus_metrics():
    """Example using Prometheus metrics (requires prometheus_client)."""
    print("=== Example 3: Prometheus Metrics (requires 'prometheus_client') ===\n")
    
    try:
        from advanced_caching.exporters import PrometheusMetrics
        
        # Create Prometheus metrics collector
        metrics = PrometheusMetrics(namespace="myapp", subsystem="cache")
        
        @TTLCache.cached("product:{}", ttl=300, metrics=metrics)
        async def get_product(product_id: int):
            await asyncio.sleep(0.05)
            return {"id": product_id, "name": f"Product {product_id}"}
        
        # Generate some traffic
        for i in range(5):
            result = await get_product(i)
            print(f"Fetched: {result}")
        
        # Cache hits
        for i in range(3):
            result = await get_product(i)
            print(f"Cached: {result}")
        
        print("\n✓ Metrics are being collected by Prometheus")
        print("  Run prometheus_client.start_http_server(8000) to expose metrics")
        print("  Then visit http://localhost:8000/metrics\n")
        
    except ImportError:
        print("⚠ prometheus_client not installed. Run: pip install 'advanced-caching[prometheus]'\n")


def example_null_metrics():
    """Example showing zero-overhead NullMetrics for development."""
    print("=== Example 4: Zero-Overhead NullMetrics ===\n")
    
    from advanced_caching.metrics import NULL_METRICS
    
    @TTLCache.cached("config:{}", ttl=3600, metrics=NULL_METRICS)
    def get_config(env: str):
        return {"env": env, "debug": True}
    
    # Metrics are completely disabled - zero overhead
    result = get_config("dev")
    print(f"Config: {result}")
    print("✓ No metrics overhead (perfect for development)\n")


def main():
    """Run all examples."""
    print("=" * 60)
    print("Advanced Caching Metrics Examples")
    print("=" * 60 + "\n")
    
    # Synchronous examples
    example_basic_metrics()
    example_memory_tracking()
    example_null_metrics()
    
    # Async examples
    print("Running async examples...")
    asyncio.run(example_prometheus_metrics())
    
    print("=" * 60)
    print("Examples completed!")
    print("=" * 60)


if __name__ == "__main__":
    main()
