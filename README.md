# advanced-caching

[![PyPI version](https://img.shields.io/pypi/v/advanced-caching.svg)](https://pypi.org/project/advanced-caching/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Production-ready caching library** for Python with TTL, stale-while-revalidate (SWR), and background refresh.  
Type-safe, fast, thread-safe, async-friendly, and framework-agnostic.

> Issues & feature requests: [new issue](https://github.com/agkloop/advanced_caching/issues/new)

---

## Table of Contents
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Key Templates](#key-templates)
- [Storage Backends](#storage-backends)
  - [InMemCache](#inmemcache)
  - [RedisCache & Serializers](#rediscache--serializers)
  - [HybridCache (L1 + L2)](#hybridcache-l1--l2)
  - [ChainCache (multi-level)](#chaincache-multi-level)
  - [Custom Storage](#custom-storage)
- [API Reference](#api-reference)
- [Testing & Benchmarks](#testing--benchmarks)
- [Use Cases](#use-cases)
- [Comparison](#comparison)
- [Contributing](#contributing)
- [License](#license)
- [BGCache (Background)](#bgcache-background)
  - [Production example](docs/bgcache.md)

---

## Installation

```bash
uv pip install advanced-caching            # core
uv pip install "advanced-caching[redis]"  # Redis support
# pip works too
````

---

## Quick Start

```python
from advanced_caching import TTLCache, SWRCache, BGCache

# Sync function
@TTLCache.cached("user:{}", ttl=300)
def get_user(user_id: int) -> dict:
    return db.fetch(user_id)

# Async function (works natively)
@TTLCache.cached("user:{}", ttl=300)
async def get_user_async(user_id: int) -> dict:
    return await db.fetch(user_id)

# Stale-While-Revalidate (Sync)
@SWRCache.cached("product:{}", ttl=60, stale_ttl=30)
def get_product(product_id: int) -> dict:
    return api.fetch_product(product_id)

# Stale-While-Revalidate (Async)
@SWRCache.cached("async:product:{}", ttl=60, stale_ttl=30)
async def get_product_async(product_id: int) -> dict:
    return await api.fetch_product(product_id)

# Background refresh (Sync)
@BGCache.register_loader("inventory", interval_seconds=300)
def load_inventory() -> list[dict]:
    return warehouse_api.get_all_items()

# Background refresh (Async)
@BGCache.register_loader("inventory_async", interval_seconds=300)
async def load_inventory_async() -> list[dict]:
    return await warehouse_api.get_all_items()

# Configured Cache (Reusable Backend)
# Create a decorator pre-wired with a specific cache (e.g., Redis)
RedisTTL = TTLCache.configure(cache=RedisCache(redis_client))

@RedisTTL.cached("user:{}", ttl=300)
async def get_user_redis(user_id: int):
    return await db.fetch(user_id)
```

---

## Key Templates

The library supports smart key generation that handles both positional and keyword arguments seamlessly.

* **Positional Placeholder**: `"user:{}"`
  * Uses the first argument, whether passed positionally or as a keyword.
  * Example: `get_user(123)` or `get_user(user_id=123)` -> `"user:123"`

* **Named Placeholder**: `"user:{user_id}"`
  * Resolves `user_id` from keyword arguments OR positional arguments (by inspecting the function signature).
  * Example: `def get_user(user_id): ...` called as `get_user(123)` -> `"user:123"`

* **Custom Function**:
  * For complex logic, pass a callable.
  * Example1 for kw/args with default values use : `key=lambda *a, **k: f"user:{k.get('user_id', a[0])}"`
  * Example2 fns with no defaults use : `key=lambda user_id: f"user:{user_id}"`

---

## Storage Backends

- InMemCache (default): Fast, process-local
- RedisCache: Distributed in-memory
- HybridCache: L1 (memory) + L2 (Redis)
- ChainCache: Arbitrary multi-level chain (e.g., InMem -> Redis -> S3/GCS)
- S3Cache: Object storage backend (AWS)
- GCSCache: Object storage backend (Google Cloud)
- LocalFileCache: Filesystem-backed cache (per-host)

### InMemCache

Thread-safe in-memory cache with TTL.

```python
from advanced_caching import InMemCache

cache = InMemCache()
cache.set("key", "value", ttl=60)
cache.get("key")
cache.delete("key")
cache.exists("key")
cache.set_if_not_exists("key", "value", ttl=60)
cache.cleanup_expired()
```

---

### RedisCache & Serializers

```python
import redis
from advanced_caching import RedisCache, JsonSerializer

client = redis.Redis(host="localhost", port=6379)

cache = RedisCache(client, prefix="app:")
json_cache = RedisCache(client, prefix="app:json:", serializer="json")
custom_json = RedisCache(client, prefix="app:json2:", serializer=JsonSerializer())
```

---

### HybridCache (L1 + L2)

Two-level cache:

* **L1**: In-memory
* **L2**: Redis

#### Simple setup

```python
import redis
from advanced_caching import HybridCache, TTLCache

client = redis.Redis()
hybrid = HybridCache.from_redis(client, prefix="app:", l1_ttl=60)

@TTLCache.cached("user:{}", ttl=300, cache=hybrid)
def get_user(user_id: int):
    return {"id": user_id}
```

#### Manual wiring

```python
from advanced_caching import HybridCache, InMemCache, RedisCache

l1 = InMemCache()
l2 = RedisCache(client, prefix="app:")
# l2_ttl defaults to l1_ttl * 2 if not specified
hybrid = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=60)

# Explicit l2_ttl for longer L2 persistence
hybrid_long_l2 = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=60, l2_ttl=3600)
```

**TTL behavior:**
- `l1_ttl`: How long data stays in fast L1 memory cache
- `l2_ttl`: How long data persists in L2 (Redis). Defaults to `l1_ttl * 2`
- When data expires from L1 but exists in L2, it's automatically repopulated to L1

#### With BGCache using lambda factory

For lazy initialization (e.g., deferred Redis connection):

```python
from advanced_caching import BGCache, HybridCache, InMemCache, RedisCache

def get_redis_cache():
    """Lazy Redis connection factory."""
    import redis
    client = redis.Redis(host="localhost", port=6379)
    return RedisCache(client, prefix="app:")

@BGCache.register_loader(
    "config_map",
    interval_seconds=3600,
    run_immediately=True,
    cache=lambda: HybridCache(
        l1_cache=InMemCache(),
        l2_cache=get_redis_cache(),
        l1_ttl=3600,
        l2_ttl=86400  # L2 persists longer than L1
    )
)
def load_config_map() -> dict[str, dict]:
    return {"db": {"host": "localhost"}, "cache": {"ttl": 300}}

# Access nested data
db_host = load_config_map().get("db", {}).get("host")
```

---

### ChainCache (multi-level)

```python
from advanced_caching import InMemCache, RedisCache, S3Cache, ChainCache

chain = ChainCache([
    (InMemCache(), 60),                    # L1 fast
    (RedisCache(redis_client), 300),       # L2 distributed
    (S3Cache(bucket="my-cache"), 3600),   # L3 durable
])

# Write-through all levels (per-level TTL caps applied)
chain.set("user:123", {"name": "Ana"}, ttl=900)

# Read-through with promotion to faster levels
user = chain.get("user:123")
```

Notes:
- Provide per-level TTL caps in the tuple; if `None`, the passed `ttl` is used.
- `set_if_not_exists` delegates atomicity to the deepest level and backfills upper levels on success.
- `get`/`get_entry` promote hits upward for hotter reads.

---

### Object Storage Backends (S3/GCS)

Store large cached objects cheaply in AWS S3 or Google Cloud Storage.
Supports compression and metadata-based TTL checks to minimize costs.

**[📚 Full Documentation & Best Practices](docs/object-storage-caching.md)**

```python
from advanced_caching import S3Cache, GCSCache

user_cache = S3Cache(
    bucket="my-cache-bucket",
    prefix="users/",
    serializer="json",
    compress=True,
    dedupe_writes=True,  # optional: skip uploads when content unchanged (adds HEAD)
)

gcs_cache = GCSCache(
    bucket="my-cache-bucket",
    prefix="users/",
    serializer="json",
    compress=True,
    dedupe_writes=True,  # optional: skip uploads when content unchanged (adds metadata check)
)
```

### RedisCache dedupe_writes

`RedisCache(..., dedupe_writes=True)` compares the serialized payload to the stored value; if unchanged, it skips rewriting and only refreshes TTL when provided.

### LocalFileCache (filesystem)

```python
from advanced_caching import LocalFileCache

cache = LocalFileCache("/var/tmp/ac-cache", dedupe_writes=True)
cache.set("user:123", {"name": "Ana"}, ttl=300)
user = cache.get("user:123")
```

Notes: one file per key; atomic writes; optional compression and dedupe to skip rewriting identical content.

---

## BGCache (Background)

Single-writer/multi-reader pattern with background refresh and optional independent reader caches.

```python
from advanced_caching import BGCache, InMemCache

# Writer: enforced single registration per key; refreshes cache on a schedule
@BGCache.register_writer(
    "daily_config",
    interval_seconds=300,   # refresh every 5 minutes
    ttl=None,               # defaults to interval*2
    run_immediately=True,
    cache=InMemCache(),     # or RedisCache / ChainCache
)
def load_config():
    return expensive_fetch()

# Readers: read-only; keep a local cache warm by pulling from the writer's cache
reader = BGCache.get_reader(
    "daily_config",
    interval_seconds=60,    # periodically pull from source cache into local cache
    ttl=None,               # local cache TTL defaults to interval*2
    run_immediately=True,
    cache=InMemCache(),     # local cache for this process
)

# Usage
cfg = reader()   # returns value from local cache; on miss pulls once from source cache
```

Notes:
- `register_writer` enforces one writer per key globally; raises if duplicate.
- `interval_seconds` <= 0 disables scheduling; wrapper still writes-on-demand on misses.
- `run_immediately=True` triggers an initial refresh if the cache is empty.
- `get_reader` creates a read-only accessor backed by its own cache; it pulls from the provided cache (usually the writer’s cache) and optionally keeps it warm on a schedule.
- Use `cache=` on readers to override the local cache backend (e.g., InMemCache in each process) while sourcing data from the writer’s cache backend.

See `docs/bgcache.md` for a production-grade example with Redis/ChainCache, error handling, and reader-local caches.

---

## API Reference

* `TTLCache.cached(key, ttl, cache=None)`
* `SWRCache.cached(key, ttl, stale_ttl=0, cache=None)`
* `BGCache.register_loader(key, interval_seconds, ttl=None, run_immediately=True)`
* Storages:

  * `InMemCache()`
  * `RedisCache(redis_client, prefix="", serializer="pickle"|"json"|custom)`
  * `HybridCache(l1_cache, l2_cache, l1_ttl=60, l2_ttl=None)` - `l2_ttl` defaults to `l1_ttl * 2`
* Utilities:

  * `CacheEntry`
  * `CacheStorage`
  * `validate_cache_storage()`

---

## Testing & Benchmarks

```bash
uv run pytest -q
uv run python tests/benchmark.py
```

---

## Use Cases

* Web & API caching (FastAPI, Flask, Django)
* Database query caching
* SWR for upstream APIs
* Background refresh for configs & datasets
* Distributed caching with Redis
* Hybrid L1/L2 hot-path optimization

---

## Comparison

| Feature             | advanced-caching | lru_cache | cachetools | Redis  | Memcached |
| ------------------- | ---------------- | --------- | ---------- | ------ | --------- |
| TTL                 | ✅                | ❌         | ✅          | ✅      | ✅         |
| SWR                 | ✅                | ❌         | ❌          | Manual | Manual    |
| Background refresh  | ✅                | ❌         | ❌          | Manual | Manual    |
| Custom backends     | ✅ (InMem/Redis/S3/GCS/Chain) | ❌ | ❌ | N/A | N/A |
| Distributed         | ✅ (Redis, ChainCache) | ❌ | ❌ | ✅ | ✅ |
| Multi-level chain   | ✅ (ChainCache)  | ❌         | ❌          | Manual | Manual    |
| Dedupe writes       | ✅ (Redis/S3/GCS opt-in) | ❌ | ❌ | Manual | Manual |
| Async support       | ✅                | ❌         | ❌          | ✅      | ✅         |
| Type hints          | ✅                | ✅         | ✅          | ❌      | ❌         |

---

## Contributing

1. Fork the repo
2. Create a feature branch
3. Add tests
4. Run `uv run pytest`
5. Open a pull request

---

## License
MIT License – see [LICENSE](LICENSE).