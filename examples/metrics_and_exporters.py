"""
Metrics & exporters example.

Covers:
  - InMemoryMetrics shared across multiple decorated functions
  - Per-function hit/miss/latency stats
  - Custom MetricsCollector protocol implementation
  - NULL_METRICS (zero-overhead no-op)

Run:
    uv run python examples/metrics_and_exporters.py
"""

from __future__ import annotations

import json
import time
from advanced_caching import cache, InMemoryMetrics
from advanced_caching.metrics import NULL_METRICS

# ── Shared metrics collector ──────────────────────────────────────────────────
metrics = InMemoryMetrics()


@cache(60, key="user:{user_id}", metrics=metrics)
def get_user(user_id: int) -> dict:
    return {"id": user_id, "name": f"User{user_id}"}


@cache(300, key="product:{product_id}", metrics=metrics)
def get_product(product_id: int) -> dict:
    return {"id": product_id, "price": 9.99}


@cache(3600, key="config:{}", metrics=metrics)
def get_config(key: str) -> dict:
    return {"key": key, "value": "on"}


# ── Custom MetricsCollector ───────────────────────────────────────────────────
class PrintMetrics:
    """Simple logger-based metrics — implement the full protocol."""

    def record_hit(self, cache_name, key=None, metadata=None):
        print(f"    HIT  {cache_name}[{key}]")

    def record_miss(self, cache_name, key=None, metadata=None):
        print(f"    MISS {cache_name}[{key}]")

    def record_set(self, cache_name, key=None, value_size=None, metadata=None): ...
    def record_delete(self, cache_name, key=None, metadata=None): ...
    def record_latency(
        self, cache_name, operation=None, duration_seconds=None, metadata=None
    ): ...
    def record_error(
        self, cache_name, operation=None, error_type=None, metadata=None
    ): ...
    def record_memory_usage(
        self, cache_name, bytes_used=None, entry_count=None, metadata=None
    ): ...
    def record_background_refresh(
        self, cache_name, success=None, duration_seconds=None, metadata=None
    ): ...


@cache(60, key="traced:{x}", metrics=PrintMetrics())
def traced_fn(x: int) -> int:
    return x * 2


def main():
    print("\n=== Shared InMemoryMetrics ===")

    # Generate traffic
    for uid in [1, 2, 1, 3, 1]:
        get_user(uid)
    for pid in [10, 10, 11, 10]:
        get_product(pid)
    get_config("ff_dark_mode")
    get_config("ff_dark_mode")

    stats = metrics.get_stats()
    print("\n  Per-function stats:")
    for name, s in stats.get("caches", {}).items():
        print(
            f"    {name:<35}  hits={s['hits']:>3}  misses={s['misses']:>3}"
            f"  hit_rate={s['hit_rate_percent']:.0f}%"
        )

    print("\n=== Custom PrintMetrics ===")
    traced_fn(5)  # miss
    traced_fn(5)  # hit
    traced_fn(6)  # miss

    print("\n=== NULL_METRICS (zero overhead) ===")

    @cache(60, key="null_bench", metrics=NULL_METRICS)
    def fast_fn() -> int:
        return 1

    fast_fn()  # prime
    n = 500_000
    t0 = time.perf_counter()
    for _ in range(n):
        fast_fn()
    elapsed = time.perf_counter() - t0
    print(f"    NULL_METRICS: {n / elapsed / 1e6:.2f}M ops/s (no metric overhead)")


if __name__ == "__main__":
    main()
