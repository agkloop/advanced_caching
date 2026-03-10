"""
bg.write / bg.read — Single-Writer / Multi-Reader pattern.

Simulates a background worker process writing to a shared store while
multiple reader "processes" (threads here) consume from their own local mirrors.

Run:
    uv run python examples/writer_reader.py
"""

from __future__ import annotations

import time
from advanced_caching import bg, InMemCache, InMemoryMetrics

# ── Shared store (use RedisCache in production for cross-process sharing) ────
shared_store = InMemCache()
metrics = InMemoryMetrics()


# ── Writer (one per key per process) ─────────────────────────────────────────
@bg.write(
    0.1, key="exchange_rates", store=shared_store, metrics=metrics, run_immediately=True
)
def refresh_rates() -> dict:
    rates = {"USD": 1.0, "EUR": 0.92, "GBP": 0.79, "ts": time.time()}
    print(f"  [writer] refreshed → {rates}")
    return rates


# ── Readers (each gets its own private local mirror) ─────────────────────────
#   store= is optional when writer is in same process — auto-discovers
get_rates_fast = bg.read("exchange_rates", interval=0.1)  # auto-discover
get_rates_slow = bg.read("exchange_rates", interval=0.5, store=shared_store)


def main():
    print("\n=== Writer / Reader Pattern ===")
    print("Writer refreshes every 100 ms; readers poll from private mirrors.\n")

    time.sleep(0.15)  # let writer run at least once

    for i in range(4):
        fast = get_rates_fast()
        slow = get_rates_slow()
        print(f"  tick {i + 1}:  fast_reader={fast}  slow_reader={slow}")
        time.sleep(0.12)

    # Metrics report
    stats = metrics.get_stats()
    bg_stats = stats.get("background_refresh", {})
    print(f"\n  Writer refresh stats: {bg_stats}")

    bg.shutdown()
    print("  bg scheduler stopped.")


if __name__ == "__main__":
    main()
