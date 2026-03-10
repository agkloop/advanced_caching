"""
Serializers example — orjson (default), msgpack, pickle, and custom.

Serializers apply to backends that store bytes externally: RedisCache,
LocalFileCache, S3Cache, GCSCache.  InMemCache stores Python objects directly
and needs no serialization.

Run:
    uv run python examples/serializers_example.py
"""

from __future__ import annotations

import tempfile
import time
from advanced_caching import cache
from advanced_caching import serializers
from advanced_caching.storage import LocalFileCache

TMPDIR = tempfile.mkdtemp(prefix="ac_ser_")


# ── orjson (default) — fastest for JSON-serializable data ────────────────────
json_store = LocalFileCache(TMPDIR + "/json", serializer=serializers.json)


@cache(60, key="json:{x}", store=json_store)
def compute_json(x: int) -> dict:
    return {"x": x, "sq": x * x}


# ── Pickle — arbitrary Python objects, no schema ─────────────────────────────
pickle_store = LocalFileCache(TMPDIR + "/pkl", serializer=serializers.pickle)


@cache(60, key="pickle:{x}", store=pickle_store)
def compute_pickle(x: int) -> dict:
    return {"x": x, "sq": x * x}


# ── MsgPack — compact binary, ~2× faster than JSON for large payloads ─────────
try:
    serializers.msgpack.dumps({"test": 1})  # probe before creating store
    msgpack_store = LocalFileCache(TMPDIR + "/msgpack", serializer=serializers.msgpack)

    @cache(60, key="msgpack:{x}", store=msgpack_store)
    def compute_msgpack(x: int) -> dict:
        return {"x": x, "sq": x * x}

    HAS_MSGPACK = True
except (ImportError, Exception):
    HAS_MSGPACK = False


# ── Custom serializer ─────────────────────────────────────────────────────────
import json as _json


class CompactJson:
    """Minimal custom serializer using stdlib json."""

    def dumps(self, v: object) -> bytes:
        return _json.dumps(v, separators=(",", ":")).encode()

    def loads(self, b: bytes) -> object:
        return _json.loads(b)


custom_store = LocalFileCache(TMPDIR + "/custom", serializer=CompactJson())


@cache(60, key="custom:{x}", store=custom_store)
def compute_custom(x: int) -> dict:
    return {"x": x, "sq": x * x}


# ── Also show: RedisCache serializer usage (comment to run) ──────────────────
# import redis as _redis
# r = _redis.from_url("redis://localhost:6379", decode_responses=False)
# from advanced_caching import RedisCache
# redis_json_store = RedisCache(r, prefix="ser:", serializer=serializers.json)
# redis_msgpack    = RedisCache(r, prefix="mp:",  serializer=serializers.msgpack)


def bench(fn, n=10_000) -> float:
    fn(1)  # prime (write to disk)
    fn(1)  # warm (read from cache)
    t0 = time.perf_counter()
    for _ in range(n):
        fn(1)
    return time.perf_counter() - t0


def main():
    print("\n=== Serializer Throughput Comparison (10k cached hits) ===\n")
    print("  Backend: LocalFileCache (disk I/O; Redis/InMem would be faster)\n")

    results: dict[str, float] = {
        "orjson (default)": bench(compute_json),
        "pickle          ": bench(compute_pickle),
        "custom json     ": bench(compute_custom),
    }
    if HAS_MSGPACK:
        results["msgpack         "] = bench(compute_msgpack)
    else:
        print(
            "  (msgpack not installed — skip: pip install 'advanced-caching[msgpack]')\n"
        )

    for name, elapsed in sorted(results.items(), key=lambda x: x[1]):
        ops = 10_000 / elapsed
        print(f"  {name}  {ops / 1e3:>6.1f}k ops/s  ({elapsed * 1000:.0f} ms total)")

    print()
    print("  Note: serializer choice matters most for Redis/S3/GCS backends.")
    print("  InMemCache stores Python objects directly — no serialization overhead.")
    print()


if __name__ == "__main__":
    main()
