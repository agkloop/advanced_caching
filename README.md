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
  - [Custom Storage](#custom-storage)
- [API Reference](#api-reference)
- [Testing & Benchmarks](#testing--benchmarks)
- [Use Cases](#use-cases)
- [Comparison](#comparison)
- [Contributing](#contributing)
- [License](#license)

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
```

---

## Key Templates

* `"user:{}"` → first positional argument
* `"user:{user_id}"` → named argument
* Custom:

```python
key=lambda *a, **k: f"user:{k.get('user_id', a[0])}"
```

---

## Storage Backends

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

#### Custom Serializer Example (msgpack)

```python
import msgpack

class MsgpackSerializer:
    handles_entries = False

    @staticmethod
    def dumps(obj):
        return msgpack.packb(obj, use_bin_type=True)

    @staticmethod
    def loads(data):
        return msgpack.unpackb(data, raw=False)
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

### Custom Storage

Implement the `CacheStorage` protocol.

#### File-based example

```python
import json, time
from pathlib import Path
from advanced_caching import CacheEntry, CacheStorage, TTLCache, validate_cache_storage

class FileCache(CacheStorage):
    def __init__(self, directory="/tmp/cache"):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.dir / f"{key.replace(':','_')}.json"

    def get_entry(self, key):
        p = self._path(key)
        if not p.exists():
            return None
        data = json.loads(p.read_text())
        return CacheEntry(**data)

    def set_entry(self, key, entry, ttl=None):
        self._path(key).write_text(json.dumps(entry.__dict__))

    def get(self, key):
        e = self.get_entry(key)
        return e.value if e and e.is_fresh() else None

    def set(self, key, value, ttl=0):
        now = time.time()
        self.set_entry(key, CacheEntry(value, now + ttl, now))

    def delete(self, key):
        self._path(key).unlink(missing_ok=True)

    def exists(self, key):
        return self.get(key) is not None

    def set_if_not_exists(self, key, value, ttl):
        if self.exists(key):
            return False
        self.set(key, value, ttl)
        return True

cache = FileCache()
assert validate_cache_storage(cache)
```

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

| Feature            | advanced-caching | lru_cache | cachetools | Redis  | Memcached |
| ------------------ | ---------------- | --------- | ---------- | ------ | --------- |
| TTL                | ✅                | ❌         | ✅          | ✅      | ✅         |
| SWR                | ✅                | ❌         | ❌          | Manual | Manual    |
| Background refresh | ✅                | ❌         | ❌          | Manual | Manual    |
| Custom backends    | ✅                | ❌         | ❌          | N/A    | N/A       |
| Distributed        | ✅                | ❌         | ❌          | ✅      | ✅         |
| Async support      | ✅                | ❌         | ❌          | ✅      | ✅         |
| Type hints         | ✅                | ✅         | ✅          | ❌      | ❌         |

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