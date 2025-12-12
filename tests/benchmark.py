from __future__ import annotations

import json
import os
import random
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from statistics import mean, median, stdev
from typing import Callable, Iterable

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if _SRC_DIR.exists() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from advanced_caching import BGCache, SWRCache, TTLCache


@dataclass(frozen=True)
class Config:
    seed: int = 12345
    work_ms: float = 5.0
    warmup: int = 10
    runs: int = 300
    mixed_key_space: int = 100
    mixed_runs: int = 500


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


def io_bound_call(user_id: int) -> dict:
    """Simulate a typical small I/O call (db/API)."""
    time.sleep(CFG.work_ms / 1000.0)
    return {"id": user_id, "name": f"User{user_id}"}


def _timed(fn: Callable[[], object], warmup: int, runs: int) -> list[float]:
    for _ in range(warmup):
        fn()

    times: list[float] = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    return times


def bench(
    label: str, fn: Callable[[], object], *, notes: str, warmup: int, runs: int
) -> Stats:
    times = _timed(fn, warmup=warmup, runs=runs)
    return Stats(
        label=label,
        notes=notes,
        runs=runs,
        median_ms=median(times),
        mean_ms=mean(times),
        stdev_ms=(stdev(times) if len(times) > 1 else 0.0),
    )


def print_table(title: str, rows: list[Stats]) -> None:
    print("\n" + title)
    print("-" * len(title))
    print(
        f"{'Strategy':<22} {'Median (ms)':>12} {'Mean (ms)':>12} {'Stdev (ms)':>12}  Notes"
    )
    for r in rows:
        print(
            f"{r.label:<22} {r.median_ms:>12.4f} {r.mean_ms:>12.4f} {r.stdev_ms:>12.4f}  {r.notes}"
        )


def keys_unique(n: int) -> Iterable[int]:
    for i in range(1, n + 1):
        yield i


def keys_mixed(n: int, key_space: int) -> list[int]:
    return [RNG.randint(1, key_space) for _ in range(n)]


def scenario_cold() -> list[Stats]:
    """Always-miss: new key every call."""
    cold_keys = iter(keys_unique(CFG.runs + CFG.warmup))
    baseline = bench(
        "baseline",
        lambda: io_bound_call(next(cold_keys)),
        notes="no cache",
        warmup=CFG.warmup,
        runs=CFG.runs,
    )

    ttl_counter = iter(keys_unique(CFG.runs + CFG.warmup))

    @TTLCache.cached("user:{}", ttl=60)
    def ttl_fn(user_id: int) -> dict:
        return io_bound_call(user_id)

    ttl = bench(
        "TTLCache",
        lambda: ttl_fn(next(ttl_counter)),
        notes="miss + store",
        warmup=CFG.warmup,
        runs=CFG.runs,
    )

    swr_counter = iter(keys_unique(CFG.runs + CFG.warmup))

    @SWRCache.cached("user:{}", ttl=60, stale_ttl=30)
    def swr_fn(user_id: int) -> dict:
        return io_bound_call(user_id)

    swr = bench(
        "SWRCache",
        lambda: swr_fn(next(swr_counter)),
        notes="miss + store",
        warmup=CFG.warmup,
        runs=CFG.runs,
    )

    return [baseline, ttl, swr]


def scenario_hot() -> list[Stats]:
    """Always-hit: same key every call."""
    baseline = bench(
        "baseline",
        lambda: io_bound_call(1),
        notes="no cache",
        warmup=max(2, CFG.warmup // 2),
        runs=max(50, CFG.runs),
    )

    @TTLCache.cached("user:{}", ttl=60)
    def ttl_fn(user_id: int) -> dict:
        return io_bound_call(user_id)

    ttl_fn(1)
    ttl = bench(
        "TTLCache",
        lambda: ttl_fn(1),
        notes="hit",
        warmup=CFG.warmup,
        runs=CFG.runs,
    )

    @SWRCache.cached("user:{}", ttl=60, stale_ttl=30)
    def swr_fn(user_id: int) -> dict:
        return io_bound_call(user_id)

    swr_fn(1)
    swr = bench(
        "SWRCache",
        lambda: swr_fn(1),
        notes="fresh hit",
        warmup=CFG.warmup,
        runs=CFG.runs,
    )

    @BGCache.register_loader("bench_user", interval_seconds=60, run_immediately=True)
    def bg_user() -> dict:
        return io_bound_call(1)

    time.sleep(0.05)
    bg = bench(
        "BGCache",
        bg_user,
        notes="preloaded",
        warmup=CFG.warmup,
        runs=CFG.runs,
    )

    return [baseline, ttl, swr, bg]


def scenario_mixed() -> list[Stats]:
    """Fixed key space: mix of hits/misses."""
    keys = keys_mixed(CFG.mixed_runs + CFG.warmup, CFG.mixed_key_space)
    it = iter(keys)
    baseline = bench(
        "baseline",
        lambda: io_bound_call(next(it)),
        notes=f"no cache (key_space={CFG.mixed_key_space})",
        warmup=CFG.warmup,
        runs=CFG.mixed_runs,
    )

    keys = keys_mixed(CFG.mixed_runs + CFG.warmup, CFG.mixed_key_space)
    it = iter(keys)

    @TTLCache.cached("user:{}", ttl=60)
    def ttl_fn(user_id: int) -> dict:
        return io_bound_call(user_id)

    ttl = bench(
        "TTLCache",
        lambda: ttl_fn(next(it)),
        notes=f"mixed (key_space={CFG.mixed_key_space})",
        warmup=CFG.warmup,
        runs=CFG.mixed_runs,
    )

    keys = keys_mixed(CFG.mixed_runs + CFG.warmup, CFG.mixed_key_space)
    it = iter(keys)

    @SWRCache.cached("user:{}", ttl=60, stale_ttl=30)
    def swr_fn(user_id: int) -> dict:
        return io_bound_call(user_id)

    swr = bench(
        "SWRCache",
        lambda: swr_fn(next(it)),
        notes=f"mixed (key_space={CFG.mixed_key_space})",
        warmup=CFG.warmup,
        runs=CFG.mixed_runs,
    )

    return [baseline, ttl, swr]


def append_json_log(
    status: str, error: str | None, sections: dict[str, list[Stats]]
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


def main() -> None:
    status = "ok"
    error: str | None = None
    sections: dict[str, list[Stats]] = {}

    print("advanced_caching benchmark (minimal)")
    print(
        f"work_ms={CFG.work_ms} seed={CFG.seed} warmup={CFG.warmup} runs={CFG.runs} mixed_runs={CFG.mixed_runs}"
    )

    try:
        sections["cold"] = scenario_cold()
        print_table("Cold (always miss)", sections["cold"])

        sections["hot"] = scenario_hot()
        print_table("Hot (always hit)", sections["hot"])

        sections["mixed"] = scenario_mixed()
        print_table("Mixed (hits + misses)", sections["mixed"])

    except KeyboardInterrupt:
        status = "interrupted"
        error = "KeyboardInterrupt"
        raise
    except Exception as e:
        status = "error"
        error = f"{type(e).__name__}: {e}"
        raise
    finally:
        try:
            BGCache.shutdown(wait=False)
        except Exception:
            pass
        append_json_log(status=status, error=error, sections=sections)


if __name__ == "__main__":
    main()
