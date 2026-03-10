"""
Profiler-friendly workload for advanced-caching.

Designed for use with Scalene, cProfile, or py-spy:

    uv run scalene tests/profile_decorators.py
    uv run python -m cProfile -s cumulative tests/profile_decorators.py
    py-spy record -o profile.svg -- python tests/profile_decorators.py

Environment:
    PROFILE_N  number of iterations (default: 2_000_000)
    PROFILE_ASYNC  set to "1" to also run async workload
"""

from __future__ import annotations

import asyncio
import os
import time

from advanced_caching import cache, bg, InMemCache

N = int(os.getenv("PROFILE_N", "2_000_000"))
RUN_ASYNC = os.getenv("PROFILE_ASYNC", "0") == "1"


# ── Workloads ────────────────────────────────────────────────────────────────


@cache(3600, key="prof_static")
def static_key() -> int:
    return 1


@cache(3600, key="prof:{x}")
def named_key(x: int) -> int:
    return x


@cache(0.0001, stale=3600, key="prof_swr")
def swr_stale() -> int:
    return 1


@cache(3600, key="prof_async")
async def async_hit() -> int:
    return 1


def run_sync() -> None:
    # Prime caches
    static_key()
    named_key(1)
    swr_stale()
    time.sleep(0.001)  # go stale but inside SWR window

    print(f"Running {N:,} sync iterations...")
    t0 = time.perf_counter()

    for _ in range(N):
        static_key()

    t1 = time.perf_counter()
    for _ in range(N):
        named_key(1)

    t2 = time.perf_counter()
    for _ in range(N):
        swr_stale()

    t3 = time.perf_counter()

    print(
        f"  static_key : {(t1 - t0) * 1000:.0f} ms  ({N / (t1 - t0) / 1e6:.2f}M ops/s)"
    )
    print(
        f"  named_key  : {(t2 - t1) * 1000:.0f} ms  ({N / (t2 - t1) / 1e6:.2f}M ops/s)"
    )
    print(
        f"  swr_stale  : {(t3 - t2) * 1000:.0f} ms  ({N / (t3 - t2) / 1e6:.2f}M ops/s)"
    )


async def run_async() -> None:
    await async_hit()  # prime
    n = N // 10  # async is slower; use fewer iterations

    print(f"\nRunning {n:,} async iterations...")
    t0 = time.perf_counter()
    for _ in range(n):
        await async_hit()
    elapsed = time.perf_counter() - t0
    print(f"  async_hit  : {elapsed * 1000:.0f} ms  ({n / elapsed / 1e6:.2f}M ops/s)")


def main() -> None:
    run_sync()
    if RUN_ASYNC:
        asyncio.run(run_async())


if __name__ == "__main__":
    main()
