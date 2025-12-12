from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if _SRC_DIR.exists() and str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from advanced_caching import BGCache, SWRCache, TTLCache


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def main() -> None:
    # This script is designed for profilers (e.g., Scalene):
    # - No per-iteration timing
    # - Minimal I/O / printing
    n = _env_int("PROFILE_N", 2_000_000)

    def cheap_work(x: int) -> int:
        return x + 1

    @TTLCache.cached("ttl:{}", ttl=60)
    def ttl_fn(x: int) -> int:
        return cheap_work(x)

    @SWRCache.cached("swr:{}", ttl=60, stale_ttl=30)
    def swr_fn(x: int) -> int:
        return cheap_work(x)

    @BGCache.register_loader("bg", interval_seconds=60)
    def bg_loader() -> int:
        return cheap_work(1)

    # Warm caches.
    ttl_fn(1)
    swr_fn(1)
    bg_loader()

    # Hot-path loops.
    for _ in range(n):
        ttl_fn(1)
    for _ in range(n):
        swr_fn(1)
    for _ in range(n):
        bg_loader()

    # Miss-path loops (smaller: avoid excessive memory growth).
    miss_n = max(10_000, n // 100)
    for i in range(miss_n):
        ttl_fn(i)
    for i in range(miss_n):
        swr_fn(i)

    # Give any background work a moment to settle.
    time.sleep(0.05)

    # Stop background scheduler thread to avoid profiler noise.
    BGCache.shutdown(wait=False)


if __name__ == "__main__":
    main()
