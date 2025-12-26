# BGCache: Single-Writer / Multi-Reader (Production Example)

This guide shows a production-grade split of BGCache writer and readers, including background refresh, error handling, and per-process reader caches.

## Goals
- One writer per key (enforced) refreshing a shared cache (e.g., Redis or ChainCache).
- Many readers in different processes/threads pulling from the writer’s cache and keeping a local L1 warm.
- Graceful error handling, optional run-immediately load, and configurable intervals/TTLs.

## Recommended Topology
- **Writer cache**: a shared backend (e.g., `RedisCache`, `ChainCache` with Redis+S3, or plain `InMemCache` if single-process).
- **Reader cache**: a fast local cache per process (e.g., `InMemCache`) that periodically pulls from the writer cache.

## End-to-end Example (multiple writers/readers, object storage cold tier)

```python
import logging
from advanced_caching import BGCache, InMemCache, RedisCache, ChainCache

logger = logging.getLogger(__name__)

# Shared writer cache: InMem L1 + Redis L2 + object storage L3 (S3/GCS/local file)
shared_writer_cache = ChainCache([
    (InMemCache(), 30),
    (RedisCache(redis_client, dedupe_writes=True), 300),
    # Choose one cold tier:
    # (S3Cache(bucket="my-cache", dedupe_writes=True), 3600),
    # (GCSCache(bucket="my-cache", dedupe_writes=True), 3600),
    # (LocalFileCache("/var/tmp/bgcache", dedupe_writes=True), 3600),
])

# Writer 1: daily config
@BGCache.register_writer(
    "daily_config",
    interval_seconds=300,
    ttl=None,
    run_immediately=True,
    on_error=lambda e: logger.error("daily_config writer failed", exc_info=e),
    cache=shared_writer_cache,
)
def refresh_config():
    return load_config_from_db_or_api()

# Writer 2: feature flags
@BGCache.register_writer(
    "feature_flags",
    interval_seconds=120,
    ttl=None,
    run_immediately=True,
    on_error=lambda e: logger.error("feature_flags writer failed", exc_info=e),
    cache=shared_writer_cache,
)
def refresh_flags():
    return load_flags_from_control_plane()

# Readers: each process uses its own local cache and pulls from the writer cache
reader_local_cache = InMemCache()

get_config = BGCache.get_reader(
    "daily_config",
    interval_seconds=60,
    ttl=None,
    run_immediately=True,
    on_error=lambda e: logger.warning("daily_config reader pull failed", exc_info=e),
    cache=shared_writer_cache,  # source cache (writer’s cache, includes cold tier)
)

get_flags = BGCache.get_reader(
    "feature_flags",
    interval_seconds=30,
    ttl=None,
    run_immediately=True,
    on_error=lambda e: logger.warning("feature_flags reader pull failed", exc_info=e),
    cache=shared_writer_cache,
)

# Usage in app code
cfg = get_config()      # from local reader cache; on miss pulls once from writer cache
flags = get_flags()     # same pattern for feature flags
```

### Why this works well
- **Single writer enforced**: `register_writer` raises if the key is registered twice.
- **Background refresh**: writer schedules updates; readers schedule pulls from writer cache.
- **Local read performance**: readers serve from per-process `InMemCache`, reducing Redis/object-store round-trips.
- **Dedupe writes**: `dedupe_writes=True` on RedisCache avoids redundant writes (and refreshes TTL when unchanged).

### Tuning knobs
- `interval_seconds`: writer refresh period; reader pull period. Set to `0` to disable scheduling and rely on on-demand fetch.
- `ttl`: defaults to `interval_seconds * 2` when not provided. For readers, this is the local cache TTL.
- `run_immediately`: seed cache on startup if empty.
- `on_error`: handle/log exceptions from the writer refresh job.
- `cache`: use a distributed cache for the writer; for readers, this is the *source* cache they pull from, while they maintain their own local cache internally.

### Async variants
- Both writer and reader functions can be `async def`; BGCache picks the appropriate scheduler (AsyncIOScheduler / BackgroundScheduler). The reader returned is sync callable but can call async sources when provided.

### Using ChainCache for deeper hierarchies
- Cold tiers: S3Cache, GCSCache, LocalFileCache can sit behind Redis in ChainCache for durable or per-host persistence.
  - S3/GCS: set `dedupe_writes=True` to avoid rewriting unchanged blobs (uses metadata hashes).
  - LocalFileCache: per-host cache with atomic writes; useful when object storage isn’t available.
  - Tune per-level TTL caps in the ChainCache tuples.

## Operational tips
- Call `BGCache.shutdown()` in test teardown or graceful shutdown to stop schedulers.
- Keep `interval_seconds` moderately larger than your refresh latency to avoid overlaps.
- Monitor writer errors via `on_error`; consider alerts if refresh fails repeatedly.
- For high-QPS readers, keep `interval_seconds` small enough to ensure local caches stay warm.

## Minimal test harness (pytest style)

```python
import pytest
import asyncio
from advanced_caching import BGCache, InMemCache

@pytest.mark.asyncio
async def test_bgcache_writer_reader():
    calls = {"n": 0}
    writer_cache = InMemCache()

    @BGCache.register_writer("demo", interval_seconds=0.05, cache=writer_cache)
    def writer():
        calls["n"] += 1
        return {"v": calls["n"]}

    reader = BGCache.get_reader(
        "demo", interval_seconds=0.05, cache=writer_cache, run_immediately=True
    )

    await asyncio.sleep(0.1)
    v1 = reader()
    assert v1 and v1["v"] >= 1

    await asyncio.sleep(0.1)
    v2 = reader()
    assert v2 and v2["v"] >= v1["v"]

    BGCache.shutdown()
```

## Checklist for production
- [ ] Shared writer cache (Redis/ChainCache) sized and monitored
- [ ] Reader local caches sized appropriately
- [ ] `on_error` hooked for alerting
- [ ] Reasonable `interval_seconds` and `ttl`
- [ ] `BGCache.shutdown()` on service shutdown/tests
- [ ] Dedupe enabled where write amplification matters (Redis/S3/GCS)
- [ ] ChainCache tiers tuned (per-level TTL caps)
