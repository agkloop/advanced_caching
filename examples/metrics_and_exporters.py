"""
Metrics & exporters — complete guide.

Covers:
  1. How InMemoryMetrics works and what get_stats() returns
  2. Shared collector across multiple functions
  3. Custom MetricsCollector (logger / Prometheus / etc.)
  4. NULL_METRICS for zero overhead
  5. Per-layer metrics with ChainCache (InMem → Redis → S3)

Run:
    uv run python examples/metrics_and_exporters.py
"""

from __future__ import annotations

import json
import time

from advanced_caching import cache, InMemCache, ChainCache, InMemoryMetrics
from advanced_caching.metrics import NULL_METRICS
from advanced_caching.storage.utils import InstrumentedStorage

# ── 1. How get_stats() output is structured ────────────────────────────────────
#
# InMemoryMetrics.get_stats() returns:
#
#   {
#     "uptime_seconds": 12.3,
#     "caches": {
#       "<func_name>": {
#         "hits": 7, "misses": 3, "sets": 3, "deletes": 0,
#         "hit_rate_percent": 70.0
#       }
#     },
#     "latency": {
#       "<func_name>.get": {"count": 10, "p50_ms": 0.01, "p95_ms": 0.05, "p99_ms": 0.12, "avg_ms": 0.02}
#       "<func_name>.set": {"count": 3,  "p50_ms": 0.02, ...}
#     },
#     "errors": {
#       "<func_name>.get": {"RedisConnectionError": 2}
#     },
#     "memory": {
#       "<func_name>": {"bytes": 4096, "entries": 3, "mb": 0.004}
#     },
#     "background_refresh": {
#       "<cache_key>": {"success": 15, "failure": 0}
#     }
#   }
#
# The "caches" key groups by function (or cache_name). "latency" shows
# per-operation percentiles (p50/p95/p99) measured in milliseconds.


# ── 2. Shared collector across multiple functions ─────────────────────────────

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


# ── 3. Custom MetricsCollector ────────────────────────────────────────────────
#
# Implement any subset of the MetricsCollector protocol.
# Unused methods can be no-ops (`...`).


class PrintMetrics:
    """Logs every cache hit/miss to stdout — useful for debugging."""

    def record_hit(self, cache_name, key=None, metadata=None):
        print(f"    ✓ HIT   {cache_name}  key={key}")

    def record_miss(self, cache_name, key=None, metadata=None):
        print(f"    ✗ MISS  {cache_name}  key={key}")

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


# ── 4. NULL_METRICS — zero overhead ───────────────────────────────────────────
#
# Pass NULL_METRICS (or omit the metrics= arg entirely) on hot paths.
# Python optimises away the no-op calls.


@cache(60, key="fast:{}", metrics=NULL_METRICS)
def fast_fn(x: int) -> int:
    return x


# ── 5. Per-layer ChainCache metrics ───────────────────────────────────────────
#
# Wrap each layer with InstrumentedStorage *before* passing it to
# ChainCache.build(). Every layer gets its own cache_name, so get_stats()
# shows hits/misses/latency broken down by tier.
#
#   Real production setup:
#     L1 = InstrumentedStorage(InMemCache(),      m, "L1:inmem")
#     L2 = InstrumentedStorage(RedisCache(client), m, "L2:redis")
#     L3 = InstrumentedStorage(S3Cache(s3, "bkt"), m, "L3:s3")
#     chain = ChainCache.build(L1, L2, L3, ttls=[60, 300, 3600])
#     @cache(3600, key="catalog:{pg}", store=chain)
#
# Here we use three InMemCache instances as stand-ins for Redis and S3.

chain_metrics = InMemoryMetrics()

_l1 = InstrumentedStorage(InMemCache(), chain_metrics, "L1:inmem")
_l2 = InstrumentedStorage(InMemCache(), chain_metrics, "L2:redis")
_l3 = InstrumentedStorage(InMemCache(), chain_metrics, "L3:s3")
chain = ChainCache.build(_l1, _l2, _l3, ttls=[60, 300, 3600])


@cache(3600, key="catalog:{page}", store=chain)
def get_catalog(page: int) -> list:
    return [{"id": i} for i in range(page * 10, page * 10 + 10)]


def _print_chain_stats() -> None:
    stats = chain_metrics.get_stats()
    caches = stats.get("caches", {})
    print(
        f"\n  {'Layer':<14}  {'hits':>5}  {'misses':>6}  {'sets':>5}  {'hit_rate':>9}"
    )
    print(f"  {'-' * 14}  {'-' * 5}  {'-' * 6}  {'-' * 5}  {'-' * 9}")
    for layer in ("L1:inmem", "L2:redis", "L3:s3"):
        s = caches.get(layer, {})
        hits = s.get("hits", 0)
        misses = s.get("misses", 0)
        sets = s.get("sets", 0)
        hit_rate = s.get("hit_rate_percent", 0.0)
        print(f"  {layer:<14}  {hits:>5}  {misses:>6}  {sets:>5}  {hit_rate:>8.0f}%")


def main() -> None:
    # ── Section 2: shared metrics ────────────────────────────────────────────
    print("\n=== 2. Shared InMemoryMetrics across functions ===")

    for uid in [1, 2, 1, 3, 1]:  # uid=1 hits twice, uid=2/3 miss once each
        get_user(uid)
    for pid in [10, 10, 11, 10]:  # pid=10 hits twice, pid=11 misses once
        get_product(pid)
    get_config("dark_mode")  # miss
    get_config("dark_mode")  # hit

    stats = metrics.get_stats()
    print(f"\n  {'Function':<35}  {'hits':>5}  {'misses':>6}  {'hit_rate':>9}")
    print(f"  {'-' * 35}  {'-' * 5}  {'-' * 6}  {'-' * 9}")
    for name, s in stats.get("caches", {}).items():
        print(
            f"  {name:<35}  {s['hits']:>5}  {s['misses']:>6}"
            f"  {s['hit_rate_percent']:>8.0f}%"
        )

    # Latency percentiles (p50 / p95 / p99) per operation
    print(f"\n  {'Operation':<40}  {'p50 ms':>7}  {'p95 ms':>7}  {'p99 ms':>7}")
    print(f"  {'-' * 40}  {'-' * 7}  {'-' * 7}  {'-' * 7}")
    for op, lat in stats.get("latency", {}).items():
        print(
            f"  {op:<40}  {lat['p50_ms']:>7.3f}  {lat['p95_ms']:>7.3f}"
            f"  {lat['p99_ms']:>7.3f}"
        )

    # ── Section 3: custom collector ──────────────────────────────────────────
    print("\n=== 3. Custom PrintMetrics (hit/miss logging) ===")
    traced_fn(5)  # miss
    traced_fn(5)  # hit
    traced_fn(6)  # miss

    # ── Section 4: NULL_METRICS ───────────────────────────────────────────────
    print("\n=== 4. NULL_METRICS (zero overhead) ===")
    fast_fn(1)  # prime
    n = 500_000
    t0 = time.perf_counter()
    for _ in range(n):
        fast_fn(1)
    elapsed = time.perf_counter() - t0
    print(f"    {n / elapsed / 1e6:.2f}M ops/s  (no metric overhead)")

    # ── Section 5: per-layer ChainCache metrics ───────────────────────────────
    print("\n=== 5. ChainCache per-layer metrics (L1:inmem → L2:redis → L3:s3) ===")

    print("\n  [cold start — all layers empty]")
    get_catalog(0)  # L1 miss, L2 miss, L3 miss → fetch from fn, set at all layers
    get_catalog(1)
    _print_chain_stats()

    print("\n  [warm — L1 has both pages]")
    get_catalog(0)  # L1 hit (no deeper lookup)
    get_catalog(1)  # L1 hit
    _print_chain_stats()

    # Simulate L1 expiry by clearing it directly
    _l1._storage.clear()
    print("\n  [L1 evicted — requests fall through to L2]")
    get_catalog(0)  # L1 miss, L2 hit → promotes back to L1
    get_catalog(1)  # L1 miss, L2 hit → promotes back to L1
    _print_chain_stats()

    # Simulate both L1 and L2 evicted
    _l1._storage.clear()
    _l2._storage.clear()
    print("\n  [L1+L2 evicted — requests fall through to L3]")
    get_catalog(0)  # L1 miss, L2 miss, L3 hit → promotes to L1+L2
    _print_chain_stats()

    print("\n  Full get_stats() snapshot (JSON):")
    print("  " + json.dumps(chain_metrics.get_stats(), indent=4).replace("\n", "\n  "))


if __name__ == "__main__":
    main()
