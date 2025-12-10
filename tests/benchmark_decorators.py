"""
Comprehensive benchmark comparing advanced caching decorators vs the most used caching decorator like cachedtools.cached.

Tests various scenarios:
- Cold cache (first access)
- Hot cache (repeated access)
- Cache with different key patterns
- Concurrent access patterns
- TTL expiry behavior
"""

import time
import random
import statistics
from typing import Callable, List

from src.advanced_caching import TTLCache


# ============================================================================
# Simple Benchmark Utilities
# ============================================================================


def benchmark_function(func: Callable, runs: int = 1000, warmup: int = 100) -> dict:
    """
    Simple benchmark utility without external dependencies.

    Returns dict with timing statistics.
    """
    # Warmup
    for _ in range(warmup):
        func()

    # Actual timing
    times = []
    for _ in range(runs):
        start = time.perf_counter()
        func()
        end = time.perf_counter()
        times.append((end - start) * 1000)  # Convert to ms

    return {
        "min": min(times),
        "max": max(times),
        "mean": statistics.mean(times),
        "median": statistics.median(times),
        "stdev": statistics.stdev(times) if len(times) > 1 else 0,
        "runs": runs,
    }


def print_result(name: str, result: dict, baseline: float = None):
    """Print benchmark result in a nice format."""
    median = result["median"]
    mean = result["mean"]
    stdev = result["stdev"]

    print(
        f"  {name:25} {median:8.4f}ms  (mean: {mean:7.4f}ms, σ: {stdev:6.4f}ms)", end=""
    )

    if baseline and baseline > 0:
        speedup = baseline / median
        if speedup > 1:
            print(f"  [{speedup:>6,.1f}x faster]", end="")
        else:
            overhead = (median / baseline - 1) * 100
            print(f"  [{overhead:>6.1f}% overhead]", end="")

    print()


# ============================================================================
# Test Functions - Simulate various workloads
# ============================================================================


def expensive_computation(x: int) -> dict:
    """Simulate 5ms expensive computation."""
    time.sleep(0.005)
    return {"input": x, "result": x * x, "computed_at": time.time()}


def database_query(user_id: int) -> dict:
    """Simulate 10ms database query."""
    time.sleep(0.01)
    return {
        "id": user_id,
        "name": f"User{user_id}",
        "email": f"user{user_id}@example.com",
        "active": True,
    }


def api_call(endpoint: str) -> dict:
    """Simulate 20ms external API call."""
    time.sleep(0.02)
    return {
        "endpoint": endpoint,
        "status": 200,
        "data": {"message": f"Response from {endpoint}"},
    }


# ============================================================================
# Benchmark Scenarios
# ============================================================================


def benchmark_cold_cache():
    """Benchmark 1: Cold cache (first access) - measures cache miss + storage."""
    print("\n" + "=" * 80)
    print("BENCHMARK 1: Cold Cache Performance (First Access)")
    print("=" * 80)
    print("Measures: Cache miss + function execution + cache storage\n")

    # Baseline: No cache
    def run_no_cache():
        expensive_computation(42)

    print("Running benchmarks...")
    baseline_result = benchmark_function(run_no_cache, runs=500, warmup=10)
    baseline = baseline_result["median"]

    # TTLCache
    @TTLCache.cached("comp:{}", ttl=1)
    def with_ttl(x):
        return expensive_computation(x)

    counter = {"val": 0}

    def run_ttl():
        counter["val"] += 1
        with_ttl(counter["val"])  # Different key each time = cold cache

    ttl_result = benchmark_function(run_ttl, runs=500, warmup=10)

    # miscutil.cached
    @miscutil.cached(ttl=1)
    def with_miscutil(x):
        return expensive_computation(x)

    counter2 = {"val": 0}

    def run_miscutil():
        counter2["val"] += 1
        with_miscutil(counter2["val"])

    misc_result = benchmark_function(run_miscutil, runs=500, warmup=10)

    # Print results
    print("\nResults:")
    print_result("No Cache (baseline)", baseline_result)
    print_result("TTLCache (cold)", ttl_result, baseline)
    print_result("miscutil.cached (cold)", misc_result, baseline)

    print(f"\n📊 Cold Cache Overhead:")
    print(f"  Baseline:        {baseline:.3f}ms")
    print(
        f"  TTLCache:        {ttl_result['median']:.3f}ms (+{ttl_result['median'] - baseline:.3f}ms, {(ttl_result['median'] / baseline - 1) * 100:.1f}% overhead)"
    )
    print(
        f"  miscutil.cached: {misc_result['median']:.3f}ms (+{misc_result['median'] - baseline:.3f}ms, {(misc_result['median'] / baseline - 1) * 100:.1f}% overhead)"
    )


def benchmark_hot_cache():
    """Benchmark 2: Hot cache (repeated access) - measures pure cache hit speed."""
    print("\n" + "=" * 80)
    print("BENCHMARK 2: Hot Cache Performance (Repeated Access)")
    print("=" * 80)
    print("Measures: Pure cache hit speed (best case scenario)\n")

    # Baseline for reference
    def run_baseline():
        database_query(123)

    print("Running benchmarks...")
    baseline_result = benchmark_function(run_baseline, runs=100, warmup=5)
    baseline = baseline_result["median"]

    # TTLCache
    @TTLCache.cached("user:{}", ttl=1)
    def get_user_ttl(user_id):
        return database_query(user_id)

    get_user_ttl(123)  # Prime cache

    def run_ttl():
        get_user_ttl(123)  # Same key = cache hit

    ttl_result = benchmark_function(run_ttl, runs=50000, warmup=1000)

    # SWRCache
    @SWRCache.cached("user:{}", ttl=1, stale_ttl=30)
    def get_user_swr(user_id):
        return database_query(user_id)

    get_user_swr(123)  # Prime cache

    def run_swr():
        get_user_swr(123)

    swr_result = benchmark_function(run_swr, runs=50000, warmup=1000)

    # miscutil.cached
    @miscutil.cached(ttl=1)
    def get_user_misc():
        return database_query(123)

    get_user_misc()  # Prime cache

    def run_misc():
        get_user_misc()

    misc_result = benchmark_function(run_misc, runs=50000, warmup=1000)

    # Print results
    print("\nResults:")
    print_result("No Cache (reference)", baseline_result)
    print_result("TTLCache (hot)", ttl_result, baseline)
    print_result("SWRCache (hot)", swr_result, baseline)
    print_result("miscutil.cached (hot)", misc_result, baseline)

    print(f"\n🚀 Hot Cache Speedup:")
    print(
        f"  TTLCache:        {ttl_result['median']:.4f}ms ({baseline / ttl_result['median']:>8,.0f}x faster)"
    )
    print(
        f"  SWRCache:        {swr_result['median']:.4f}ms ({baseline / swr_result['median']:>8,.0f}x faster)"
    )
    print(
        f"  miscutil.cached: {misc_result['median']:.4f}ms ({baseline / misc_result['median']:>8,.0f}x faster)"
    )

    # Find winner
    times = [
        ("TTLCache", ttl_result["median"]),
        ("SWRCache", swr_result["median"]),
        ("miscutil.cached", misc_result["median"]),
    ]
    times.sort(key=lambda x: x[1])
    print(f"\n🏆 Fastest: {times[0][0]} ({times[0][1]:.4f}ms)")


def benchmark_varying_keys():
    """Benchmark 3: Varying keys - realistic workload with mix of hits/misses."""
    print("\n" + "=" * 80)
    print("BENCHMARK 3: Varying Keys (Realistic Workload)")
    print("=" * 80)
    print("Measures: Mixed cache hits/misses with 100 different keys\n")

    # Baseline
    def run_baseline():
        api_call(f"endpoint_{random.randint(1, 100)}")

    print("Running benchmarks...")
    baseline_result = benchmark_function(run_baseline, runs=500, warmup=10)
    baseline = baseline_result["median"]

    # TTLCache
    @TTLCache.cached("api:{}", ttl=1)
    def call_api_ttl(endpoint):
        return api_call(endpoint)

    # Prime with some keys
    for i in range(1, 51):
        call_api_ttl(f"endpoint_{i}")

    def run_ttl():
        call_api_ttl(f"endpoint_{random.randint(1, 100)}")  # 50% hit rate

    ttl_result = benchmark_function(run_ttl, runs=5000, warmup=100)

    # miscutil.cached
    @miscutil.cached(ttl=1)
    def call_api_misc(endpoint):
        return api_call(endpoint)

    # Prime with some keys
    for i in range(1, 51):
        call_api_misc(f"endpoint_{i}")

    def run_misc():
        call_api_misc(f"endpoint_{random.randint(1, 100)}")

    misc_result = benchmark_function(run_misc, runs=5000, warmup=100)

    # Print results
    print("\nResults:")
    print_result("No Cache", baseline_result)
    print_result("TTLCache (50% hit)", ttl_result, baseline)
    print_result("miscutil.cached (50% hit)", misc_result, baseline)

    print(f"\n📈 Varying Keys Performance:")
    print(f"  Baseline:        {baseline:.2f}ms")
    print(
        f"  TTLCache:        {ttl_result['median']:.2f}ms ({baseline / ttl_result['median']:.1f}x faster)"
    )
    print(
        f"  miscutil.cached: {misc_result['median']:.2f}ms ({baseline / misc_result['median']:.1f}x faster)"
    )


def benchmark_background_loading():
    """Benchmark 4: Background loading vs on-demand caching."""
    print("\n" + "=" * 80)
    print("BENCHMARK 4: Background Loading vs On-Demand")
    print("=" * 80)
    print("Measures: BGCache (pre-loaded) vs TTLCache (on-demand)\n")

    def heavy_load():
        """Simulate 50ms heavy operation."""
        time.sleep(0.05)
        return {"data": "heavy_result", "timestamp": time.time()}

    # Baseline
    def run_baseline():
        heavy_load()

    print("Running benchmarks...")
    baseline_result = benchmark_function(run_baseline, runs=100, warmup=5)
    baseline = baseline_result["median"]

    # BGCache (pre-loaded, always ready)
    @BGCache.register_loader("heavy_data", interval_seconds=10, run_immediately=True)
    def load_heavy_bg():
        return heavy_load()

    time.sleep(0.1)  # Wait for initial load

    def run_bg():
        load_heavy_bg()  # Always from cache

    bg_result = benchmark_function(run_bg, runs=50000, warmup=1000)

    # TTLCache (on-demand)
    @TTLCache.cached("heavy", ttl=10)
    def load_heavy_ttl():
        return heavy_load()

    load_heavy_ttl()  # Prime

    def run_ttl():
        load_heavy_ttl()

    ttl_result = benchmark_function(run_ttl, runs=50000, warmup=1000)

    # miscutil.cached
    @miscutil.cached(ttl=10)
    def load_heavy_misc():
        return heavy_load()

    load_heavy_misc()  # Prime

    def run_misc():
        load_heavy_misc()

    misc_result = benchmark_function(run_misc, runs=50000, warmup=1000)

    # Print results
    print("\nResults:")
    print_result("No Cache", baseline_result)
    print_result("BGCache", bg_result, baseline)
    print_result("TTLCache", ttl_result, baseline)
    print_result("miscutil.cached", misc_result, baseline)

    print(f"\n⚡ Background vs On-Demand:")
    print(f"  Baseline:        {baseline:.2f}ms")
    print(
        f"  BGCache:         {bg_result['median']:.4f}ms ({baseline / bg_result['median']:>8,.0f}x faster) 🏆 Pre-loaded!"
    )
    print(
        f"  TTLCache:        {ttl_result['median']:.4f}ms ({baseline / ttl_result['median']:>8,.0f}x faster)"
    )
    print(
        f"  miscutil.cached: {misc_result['median']:.4f}ms ({baseline / misc_result['median']:>8,.0f}x faster)"
    )

    BGCache.shutdown()


def benchmark_memory_usage():
    """Benchmark 5: Memory efficiency comparison."""
    print("\n" + "=" * 80)
    print("BENCHMARK 5: Memory Efficiency")
    print("=" * 80)
    print("Measures: Cache with custom backend vs default\n")

    # Custom lightweight cache
    custom_cache = FastCache()

    @TTLCache.cached("item:{}", ttl=1, cache=custom_cache)
    def get_item_custom(item_id):
        time.sleep(0.001)
        return {"id": item_id}

    # Prime with 1000 items
    print("Priming caches with 1000 items...")
    for i in range(1000):
        get_item_custom(i)

    def run_custom():
        get_item_custom(random.randint(0, 999))

    custom_result = benchmark_function(run_custom, runs=10000, warmup=100)

    # Default TTLCache
    @TTLCache.cached("item:{}", ttl=1)
    def get_item_default(item_id):
        time.sleep(0.001)
        return {"id": item_id}

    for i in range(1000):
        get_item_default(i)

    def run_default():
        get_item_default(random.randint(0, 999))

    default_result = benchmark_function(run_default, runs=10000, warmup=100)

    # miscutil.cached
    @miscutil.cached(ttl=1)
    def get_item_misc(item_id):
        time.sleep(0.001)
        return {"id": item_id}

    for i in range(1000):
        get_item_misc(i)

    def run_misc():
        get_item_misc(random.randint(0, 999))

    misc_result = benchmark_function(run_misc, runs=10000, warmup=100)

    # Print results
    print("\nResults:")
    print_result("TTLCache + FastCache", custom_result)
    print_result("TTLCache (default)", default_result)
    print_result("miscutil.cached", misc_result)

    print(f"\n💾 Memory Efficiency (1000 items):")
    print(f"  TTLCache + FastCache: {custom_result['median']:.4f}ms")
    print(f"  TTLCache (default):   {default_result['median']:.4f}ms")
    print(f"  miscutil.cached:      {misc_result['median']:.4f}ms")


# ============================================================================
# Main
# ============================================================================


def main():
    """Run all benchmarks."""
    print("\n" + "█" * 80)
    print("█" + " " * 78 + "█")
    print("█" + "  COMPREHENSIVE DECORATOR BENCHMARK SUITE".center(78) + "█")
    print("█" + "  TTLCache vs SWRCache vs BGCache vs miscutil.cached".center(78) + "█")
    print("█" + " " * 78 + "█")
    print("█" * 80)

    # Run all benchmarks
    benchmark_cold_cache()
    benchmark_hot_cache()
    benchmark_varying_keys()
    benchmark_background_loading()
    benchmark_memory_usage()

    # Final summary
    print("\n" + "█" * 80)
    print("█" + " " * 78 + "█")
    print("█" + "  BENCHMARK SUMMARY".center(78) + "█")
    print("█" + " " * 78 + "█")
    print("█" * 80)

    print("\n" + "█" * 80)
    print("█" + " " * 78 + "█")
    print("█" + "  ✅ BENCHMARK COMPLETE".center(78) + "█")
    print("█" + " " * 78 + "█")
    print("█" * 80 + "\n")


def test_benchmark():
    """Pytest wrapper."""
    main()


if __name__ == "__main__":
    main()
