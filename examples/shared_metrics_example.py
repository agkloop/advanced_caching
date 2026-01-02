"""
Example demonstrating shared metrics collectors across multiple cached functions.

This shows:
1. Single InMemoryMetrics collector shared across multiple functions
2. Each function's metrics tracked separately by cache_name
3. Exposing metrics via API endpoint
"""

from advanced_caching import TTLCache, SWRCache
from advanced_caching.metrics import InMemoryMetrics
import json

# Create a single shared metrics collector
metrics = InMemoryMetrics()

# Multiple cached functions sharing the same metrics collector
@TTLCache.cached("user:{id}", ttl=60, metrics=metrics)
def get_user(id: int):
    print(f"  → Cache miss: fetching user {id} from database...")
    return {"id": id, "name": f"User_{id}", "role": "admin"}

@TTLCache.cached("product:{id}", ttl=300, metrics=metrics)
def get_product(id: int):
    print(f"  → Cache miss: fetching product {id} from database...")
    return {"id": id, "name": f"Product_{id}", "price": 99.99}

@SWRCache.cached("config:{key}", ttl=120, stale_ttl=600, metrics=metrics)
def get_config(key: str):
    print(f"  → Cache miss: fetching config {key}...")
    return {"key": key, "value": "enabled"}


def main():
    print("=== Shared Metrics Collector Example ===\n")
    
    # Simulate cache operations
    print("1. Cache operations:")
    print("   get_user(1):", get_user(1))  # miss
    print("   get_user(1):", get_user(1))  # hit
    print("   get_user(2):", get_user(2))  # miss
    
    print("\n   get_product(100):", get_product(100))  # miss
    print("   get_product(100):", get_product(100))  # hit
    print("   get_product(101):", get_product(101))  # miss
    print("   get_product(101):", get_product(101))  # hit
    
    print("\n   get_config('feature_x'):", get_config('feature_x'))  # miss
    print("   get_config('feature_x'):", get_config('feature_x'))  # hit
    
    # Get aggregated stats
    print("\n2. Aggregated metrics from single collector:")
    stats = metrics.get_stats()
    print(json.dumps(stats, indent=2))
    
    # Show per-function breakdown
    print("\n3. Per-function breakdown:")
    for cache_name, cache_stats in stats.get("caches", {}).items():
        print(f"\n   {cache_name}:")
        print(f"     - Hits: {cache_stats['hits']}")
        print(f"     - Misses: {cache_stats['misses']}")
        print(f"     - Hit rate: {cache_stats['hit_rate_percent']:.1f}%")


if __name__ == "__main__":
    main()
