"""
Benchmark harness for advanced-caching hot paths.

Usage:
    uv run python tests/benchmark.py
    BENCH_N=200000 uv run python tests/benchmark.py
"""

from __future__ import annotations

import asyncio
import os
import statistics
import time
from typing import Any

from advanced_caching import cache, bg, InMemCache, ChainCache
from advanced_caching.metrics import InMemoryMetrics

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
N = int(os.getenv("BENCH_N", "100000"))
WARMUP = 1000


def _timer(fn, n: int) -> tuple[float, float]:
    """Return (total_seconds, ops_per_second)."""
    for _ in range(WARMUP):
        fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    elapsed = time.perf_counter() - t0
    return elapsed, n / elapsed


async def _atimer(coro_fn, n: int) -> tuple[float, float]:
    for _ in range(WARMUP):
        await coro_fn()
    t0 = time.perf_counter()
    for _ in range(n):
        await coro_fn()
    elapsed = time.perf_counter() - t0
    return elapsed, n / elapsed


def _row(label: str, elapsed: float, ops: float) -> None:
    print(f"  {label:<42}  {ops / 1e6:>6.2f}M ops/s  ({elapsed * 1000:.1f} ms total)")


# ---------------------------------------------------------------------------
# Benchmarks
# ---------------------------------------------------------------------------


def bench_inmem_raw():
    """Baseline: raw InMemCache.get() / .set()."""
    store = InMemCache()
    store.set("k", {"v": 1}, ttl=3600)
    elapsed, ops = _timer(lambda: store.get("k"), N)
    _row("InMemCache.get() raw", elapsed, ops)


def bench_cache_sync_hit():
    """@cache sync hit path (static key)."""

    @cache(3600, key="bench_sync")
    def fn() -> dict:
        return {"v": 1}

    fn()  # prime
    elapsed, ops = _timer(fn, N)
    _row("@cache sync hit (static key)", elapsed, ops)


def bench_cache_sync_keyed():
    """@cache sync with named key template."""

    @cache(3600, key="bench:{user_id}")
    def get_user(user_id: int) -> dict:
        return {"id": user_id}

    get_user(1)  # prime
    elapsed, ops = _timer(lambda: get_user(1), N)
    _row("@cache sync hit (named key)", elapsed, ops)


def bench_cache_async_hit():
    """@cache async hit path."""

    @cache(3600, key="bench_async")
    async def fn() -> dict:
        return {"v": 1}

    async def run():
        await fn()  # prime
        elapsed, ops = await _atimer(fn, N)
        _row("@cache async hit (static key)", elapsed, ops)

    asyncio.run(run())


def bench_cache_swr_hit():
    """@cache SWR path — serve stale, no refresh triggered (stale window)."""

    @cache(0.0001, stale=3600, key="bench_swr")
    def fn() -> dict:
        return {"v": 1}

    fn()  # prime
    time.sleep(0.001)  # go stale but inside window
    elapsed, ops = _timer(fn, N)
    _row("@cache SWR stale-serve", elapsed, ops)


def bench_cache_miss():
    """@cache sync miss + set (measures miss path overhead)."""
    calls = {"n": 0}

    @cache(0, key="bench_miss:{x}")  # ttl=0 → always miss
    def fn(x: int) -> int:
        calls["n"] += 1
        return x

    elapsed, ops = _timer(lambda: fn(1), N)
    _row("@cache sync miss (ttl=0)", elapsed, ops)


def bench_chain_cache():
    """ChainCache (L1 InMem + L2 InMem) hit on L1."""
    l1, l2 = InMemCache(), InMemCache()
    chain = ChainCache.build(l1, l2, ttls=[60, 3600])

    @cache(3600, key="chain_bench", store=chain)
    def fn() -> dict:
        return {"v": 1}

    fn()  # prime
    elapsed, ops = _timer(fn, N)
    _row("@cache ChainCache L1 hit", elapsed, ops)


def bench_bg_read():
    """bg.read() callable (local dict lookup only)."""
    store = InMemCache()
    store.set("bg_bench", {"v": 1}, ttl=3600)
    reader = bg.read("bg_bench", interval=60, store=store)
    elapsed, ops = _timer(reader, N)
    _row("bg.read() local hit", elapsed, ops)
    bg.shutdown()


def bench_with_metrics():
    """@cache + InMemoryMetrics overhead."""
    m = InMemoryMetrics()

    @cache(3600, key="bench_metrics", metrics=m)
    def fn() -> dict:
        return {"v": 1}

    fn()  # prime
    elapsed, ops = _timer(fn, N)
    _row("@cache sync hit + InMemoryMetrics", elapsed, ops)


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def main() -> None:
    print(f"\n{'=' * 65}")
    print(f"  advanced-caching benchmark  ·  N={N:,} iterations per test")
    print(f"{'=' * 65}")

    suites = [
        ("Storage baseline", [bench_inmem_raw]),
        (
            "@cache decorator",
            [
                bench_cache_sync_hit,
                bench_cache_sync_keyed,
                bench_cache_async_hit,
                bench_cache_swr_hit,
                bench_cache_miss,
            ],
        ),
        ("Multi-level", [bench_chain_cache]),
        ("bg writer/reader", [bench_bg_read]),
        ("With metrics", [bench_with_metrics]),
    ]

    for section, fns in suites:
        print(f"\n  ── {section}")
        for fn in fns:
            fn()

    print(f"\n{'=' * 65}\n")


if __name__ == "__main__":
    main()
