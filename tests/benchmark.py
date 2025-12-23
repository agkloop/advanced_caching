"""
Benchmarks for advanced_caching (Async-only architecture).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev
from typing import Dict, List

from advanced_caching import BGCache, SWRCache, TTLCache


# ---------------------------------------------------------------------------
# Config + helpers
# ---------------------------------------------------------------------------


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Config:
    seed: int = 12345
    work_ms: float = 5.0
    warmup: int = 10
    runs: int = 300
    mixed_key_space: int = 100
    mixed_runs: int = 500


CFG = Config(
    seed=_env_int("BENCH_SEED", 12345),
    work_ms=_env_float("BENCH_WORK_MS", 5.0),
    warmup=_env_int("BENCH_WARMUP", 10),
    runs=_env_int("BENCH_RUNS", 300),
    mixed_key_space=_env_int("BENCH_MIXED_KEY_SPACE", 100),
    mixed_runs=_env_int("BENCH_MIXED_RUNS", 500),
)
RNG = random.Random(CFG.seed)


@dataclass(frozen=True)
class Stats:
    label: str
    notes: str
    runs: int
    median_ms: float
    mean_ms: float
    stdev_ms: float


async def async_io_bound_call(user_id: int) -> dict:
    await asyncio.sleep(CFG.work_ms / 1000.0)
    return {"id": user_id, "name": f"User{user_id}"}


async def _timed_async(fn, warmup: int, runs: int) -> List[float]:
    for _ in range(warmup):
        await fn()
    out: List[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        await fn()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def stats_from_samples(
    label: str, notes: str, runs: int, samples: List[float]
) -> Stats:
    return Stats(
        label,
        notes,
        runs,
        median(samples),
        mean(samples),
        stdev(samples) if len(samples) > 1 else 0.0,
    )


def print_table(title: str, rows: List[Stats]) -> None:
    print("\n" + title)
    print("-" * len(title))
    print(
        f"{'Strategy':<22} {'Median (ms)':>12} {'Mean (ms)':>12} {'Stdev (ms)':>12}  Notes"
    )
    for r in rows:
        print(
            f"{r.label:<22} {r.median_ms:>12.4f} {r.mean_ms:>12.4f} {r.stdev_ms:>12.4f}  {r.notes}"
        )


def append_json_log(
    status: str, error: str | None, sections: Dict[str, List[Stats]]
) -> None:
    payload = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "status": status,
        "error": error,
        "command": "python " + " ".join(sys.argv),
        "python": sys.version.split()[0],
        "config": {
            "seed": CFG.seed,
            "work_ms": CFG.work_ms,
            "warmup": CFG.warmup,
            "runs": CFG.runs,
            "mixed_key_space": CFG.mixed_key_space,
            "mixed_runs": CFG.mixed_runs,
        },
        "results": {
            name: [
                {
                    "label": s.label,
                    "notes": s.notes,
                    "runs": s.runs,
                    "median_ms": round(s.median_ms, 6),
                    "mean_ms": round(s.mean_ms, 6),
                    "stdev_ms": round(s.stdev_ms, 6),
                }
                for s in rows
            ]
            for name, rows in sections.items()
        },
    }
    try:
        log_path = Path(__file__).resolve().parent.parent / "benchmarks.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
    except Exception:
        pass


def shutdown_schedulers() -> None:
    try:
        BGCache.shutdown(wait=False)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


async def scenario_hot_hits() -> List[Stats]:
    """Benchmark hot cache hits for all strategies."""

    # 1. TTLCache
    @TTLCache.cached("bench:ttl:{}", ttl=60)
    async def ttl_fn(user_id: int) -> dict:
        return await async_io_bound_call(user_id)

    # Prime cache
    await ttl_fn(1)

    ttl_samples = await _timed_async(
        lambda: ttl_fn(1), warmup=CFG.warmup, runs=CFG.runs
    )
    ttl_stats = stats_from_samples("TTLCache", "hot hit", CFG.runs, ttl_samples)

    # 2. SWRCache
    @SWRCache.cached("bench:swr:{}", ttl=60, stale_ttl=30)
    async def swr_fn(user_id: int) -> dict:
        return await async_io_bound_call(user_id)

    # Prime cache
    await swr_fn(1)

    swr_samples = await _timed_async(
        lambda: swr_fn(1), warmup=CFG.warmup, runs=CFG.runs
    )
    swr_stats = stats_from_samples("SWRCache", "hot hit", CFG.runs, swr_samples)

    # 3. BGCache
    @BGCache.register_loader("bench:bg", interval_seconds=60, run_immediately=True)
    async def bg_loader() -> dict:
        return await async_io_bound_call(1)

    # Wait for load
    await asyncio.sleep(0.05)

    bg_samples = await _timed_async(
        lambda: bg_loader(), warmup=CFG.warmup, runs=CFG.runs
    )
    bg_stats = stats_from_samples("BGCache", "preloaded", CFG.runs, bg_samples)

    return [ttl_stats, swr_stats, bg_stats]


async def run_benchmarks() -> Dict[str, List[Stats]]:
    return {
        "hot_hits": await scenario_hot_hits(),
    }


def main() -> None:
    status = "ok"
    error = None
    sections: Dict[str, List[Stats]] = {}

    print("advanced_caching benchmark (Async-only)")
    print(f"work_ms={CFG.work_ms} seed={CFG.seed} warmup={CFG.warmup} runs={CFG.runs}")

    try:
        sections = asyncio.run(run_benchmarks())
        print_table("Hot Cache Hits", sections["hot_hits"])
    except KeyboardInterrupt:
        status = "interrupted"
        error = "KeyboardInterrupt"
        raise
    except Exception as e:
        status = "error"
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        shutdown_schedulers()
        append_json_log(status=status, error=error, sections=sections)


if __name__ == "__main__":
    main()
