# advanced-caching

[![PyPI version](https://img.shields.io/pypi/v/advanced-caching.svg)](https://pypi.org/project/advanced-caching/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Production-ready caching library** with decorators for TTL, stale-while-revalidate (SWR), and background refresh. Type-safe, fast, and framework-agnostic.

## Quick Links

- [Installation](#installation) – Get started in 30 seconds
- [Quick Examples](#quick-start) – Copy-paste ready code
- [API Reference](#api-reference) – Full decorator & backend docs
- [Custom Storage](#custom-storage) – Implement your own backend
- [Benchmarks](#benchmarks) – See the performance gains
- [Use Cases](#use-cases) – Real-world examples

## Features

| Feature | Details |
|---------|---------|
| **TTL Caching** | Simple time-based expiration with key patterns |
| **SWR Pattern** | Serve stale data instantly, refresh in background |
| **Background Loading** | Pre-load expensive data with periodic refresh |
| **Multiple Backends** | In-memory, Redis, custom storage, or hybrid |
| **Thread-Safe** | Reentrant locks, atomic operations, concurrent-safe |
| **Type-Safe** | Full type hints, IDE-friendly, zero runtime overhead |
| **Framework-Agnostic** | Works with FastAPI, Flask, Django, async, or sync |
| **No Required Dependencies** | Only APScheduler; Redis is optional |

## Installation

### Quick Install
```bash
pip install advanced-caching
# or with uv (recommended)
uv pip install advanced-caching
# with Redis support
pip install "advanced-caching[redis]"
```

## Quick Start

### 1. TTL Cache – Time-Based Expiration
```python
from advanced_caching import TTLCache

@TTLCache.cached("user:{}", ttl=300)  # Cache 5 minutes
def get_user(user_id: int) -> dict:
    return db.fetch(user_id)

user = get_user(42)  # First: executes & caches
user = get_user(42)  # Later: instant from cache ~0.001ms
```

### 2. SWR Cache – Serve Stale, Refresh in Background
```python
from advanced_caching import SWRCache

@SWRCache.cached("product:{}", ttl=60, stale_ttl=30)
def get_product(product_id: int) -> dict:
    return api.fetch_product(product_id)

product = get_product(1)  # Returns immediately (fresh or stale)
# Stale data served instantly, refresh happens in background
```

### 3. Background Cache – Pre-Loaded Data
```python
from advanced_caching import BGCache

@BGCache.register_loader("inventory", interval_seconds=300)
def load_inventory() -> list[dict]:
    return warehouse_api.get_all_items()

inventory = load_inventory()  # Instant ~0.001ms (pre-loaded)
# Refreshes every 5 minutes automatically
```

### 4. Async Support
```python
# All decorators work with async functions
@TTLCache.cached("user:{}", ttl=300)
async def get_user_async(user_id: int) -> dict:
    return await db.fetch_user(user_id)

user = await get_user_async(42)
```

## Benchmarks

**Performance Comparison** (10ms baseline operation):

| Strategy | Time | Speedup |
|----------|------|---------|
| No Cache | 12.51 ms | 1x |
| TTLCache | 0.0010 ms | 12,000x  |
| SWRCache | 0.0014 ms | 9,100x  |
| BGCache | 0.0003 ms | 37,000x  |

Full benchmarks available in `tests/benchmark.py`.

## API Reference

### TTLCache.cached(key, ttl, cache=None)
Simple time-based cache with configurable TTL.

**Parameters:**
- `key` (str | callable): Cache key or function. String `"user:{}"` formats with first arg
- `ttl` (int): Time-to-live in seconds
- `cache` (CacheStorage): Optional custom backend (defaults to InMemCache)

**Example:**
```python
@TTLCache.cached("user:{}", ttl=300)
def get_user(user_id):
    return db.fetch(user_id)

# Custom backend
@TTLCache.cached("data:{}", ttl=60, cache=redis_cache)
def get_data(data_id):
    return api.fetch(data_id)
```

---

### SWRCache.cached(key, ttl, stale_ttl=0, cache=None, enable_lock=True)
Serve stale data instantly while refreshing in background.

**Parameters:**
- `key` (str | callable): Cache key (same format as TTLCache)
- `ttl` (int): Fresh data TTL in seconds
- `stale_ttl` (int): Grace period to serve stale data while refreshing
- `cache` (CacheStorage): Optional custom backend
- `enable_lock` (bool): Prevent thundering herd (default: True)

**Example:**
```python
@SWRCache.cached("product:{}", ttl=60, stale_ttl=30)
def get_product(product_id):
    return api.fetch_product(product_id)

# Returns immediately with fresh or stale data
# Never blocks on refresh
```

---

### BGCache.register_loader(cache_key, interval_seconds, ttl_seconds=None, run_immediately=True, on_error=None, cache=None)
Pre-load expensive data with periodic refresh.

**Parameters:**
- `cache_key` (str): Unique cache key (no formatting)
- `interval_seconds` (int): Refresh interval in seconds
- `ttl_seconds` (int): Cache TTL (defaults to interval_seconds × 2)
- `run_immediately` (bool): Load on registration (default: True)
- `on_error` (callable): Error handler function
- `cache` (CacheStorage): Optional custom backend

**Example:**
```python
@BGCache.register_loader("inventory", interval_seconds=300)
def load_inventory():
    return warehouse_api.get_items()

# Async support
@BGCache.register_loader("products", interval_seconds=300)
async def load_products():
    return await api.fetch_products()

# With error handling
@BGCache.register_loader("config", interval_seconds=60, on_error=logger.error)
def load_config():
    return settings.fetch()

# Shutdown scheduler when done
BGCache.shutdown()
```

---

### Storage Backends

#### InMemCache()
Thread-safe in-memory cache with TTL.

```python
from advanced_caching import InMemCache

cache = InMemCache()
cache.set("key", value, ttl=60)
value = cache.get("key")  # None if expired
cache.delete("key")
cache.exists("key")  # bool
cache.set_if_not_exists("key", value, ttl)  # bool
cache.cleanup_expired()  # int count
```

#### RedisCache(redis_client, prefix="")
Redis-backed distributed cache.

```python
import redis
from advanced_caching import RedisCache

client = redis.Redis(host="localhost", port=6379)
cache = RedisCache(client, prefix="app:")
# Same methods as InMemCache
```

#### HybridCache(l1_cache=None, l2_cache=None, l1_ttl=60)
Two-level cache: L1 (fast in-memory) + L2 (persistent Redis).

```python
cache = HybridCache(
    l1_cache=None,  # Defaults to InMemCache
    l2_cache=RedisCache(client),
    l1_ttl=60
)
# Hits from L1 (fast), misses fetch from L2 (distributed)
```

#### validate_cache_storage(cache) -> bool
Check if object implements CacheStorage protocol.

```python
from advanced_caching import validate_cache_storage

assert validate_cache_storage(my_cache)  # True if valid
```

#### CacheEntry
Access cache metadata (advanced use).

```python
entry = cache.get_entry("key")
if entry and entry.is_fresh():
    print(f"Age: {entry.age():.1f}s")
```

## Custom Storage

Implement the `CacheStorage` protocol for custom backends (DynamoDB, file-based, encrypted storage, etc.).

### File-Based Cache Example

```python
import json
import time
from pathlib import Path
from advanced_caching import CacheStorage, TTLCache, validate_cache_storage


class FileCache(CacheStorage):
    """File-based cache storage."""

    def __init__(self, directory: str = "/tmp/cache"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _get_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(":", "_")
        return self.directory / f"{safe_key}.json"

    def get(self, key: str):
        path = self._get_path(key)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            if data["fresh_until"] < time.time():
                path.unlink()
                return None
            return data["value"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def set(self, key: str, value, ttl: int = 0) -> None:
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")
        data = {"value": value, "fresh_until": fresh_until, "created_at": now}
        with open(self._get_path(key), "w") as f:
            json.dump(data, f)

    def delete(self, key: str) -> None:
        self._get_path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def set_if_not_exists(self, key: str, value, ttl: int) -> bool:
        if self.exists(key):
            return False
        self.set(key, value, ttl)
        return True


# Use it
cache = FileCache("/tmp/app_cache")
assert validate_cache_storage(cache)

@TTLCache.cached("user:{}", ttl=300, cache=cache)
def get_user(user_id: int):
    return {"id": user_id, "name": f"User {user_id}"}

user = get_user(42)  # Stores in /tmp/app_cache/user_42.json
```

### Best Practices

1. **TTL Handling** – Implement expiration check in `get()`
2. **Thread Safety** – Use locks for multi-threaded access
3. **Error Handling** – Handle I/O errors gracefully
4. **Cleanup** – Remove expired entries periodically
5. **Validation** – Use `validate_cache_storage()` to verify

## Testing

### Run Tests
```bash
pytest tests/test_correctness.py -v
```

### Run Benchmarks
```bash
python tests/benchmark.py
```


---

## Use Cases

### Web API Caching
```python
from fastapi import FastAPI
from advanced_caching import TTLCache

app = FastAPI()

@app.get("/users/{user_id}")
@TTLCache.cached("user:{}", ttl=300)
async def get_user(user_id: int):
    return await db.fetch_user(user_id)
```

### Database Query Caching
```python
from advanced_caching import SWRCache

@SWRCache.cached("posts:{}", ttl=60, stale_ttl=30)
def get_posts(user_id: int):
    # Serves stale posts while fetching fresh ones
    return db.query("SELECT * FROM posts WHERE user_id = ?", user_id)
```

### Configuration/Settings
```python
from advanced_caching import BGCache

@BGCache.register_loader("config", interval_seconds=300, run_immediately=True)
def load_config():
    # Pre-loaded in background, always fresh
    return settings.load_from_file()

# In app startup
app.config = load_config()
```

### Distributed Caching
```python
import redis
from advanced_caching import HybridCache, RedisCache, TTLCache

client = redis.Redis(host="redis-server")
cache = HybridCache(
    l1_cache=None,
    l2_cache=RedisCache(client),
    l1_ttl=60
)

@TTLCache.cached("data:{}", ttl=300, cache=cache)
def get_data(data_id):
    return expensive_operation(data_id)
```


---

## Comparison with Alternatives

| Feature | advanced-caching | functools.lru_cache | cachetools | Redis | Memcached |
|---------|-----------------|-------------------|-----------|-------|-----------|
| TTL Support | ✅ | ❌ | ✅ | ✅ | ✅ |
| SWR Pattern | ✅ | ❌ | ❌ | Manual | Manual |
| Background Refresh | ✅ | ❌ | ❌ | Manual | Manual |
| Custom Backends | ✅ | ❌ | ❌ | N/A | N/A |
| Distributed | ✅ (Redis) | ❌ | ❌ | ✅ | ✅ |
| Async Support | ✅ | ❌ | ❌ | ✅ | ✅ |
| Type Safe | ✅ | ✅ | ✅ | ❌ | ❌ |
| Zero Dependencies | ❌ (APScheduler) | ✅ | ✅ | ❌ | ❌ |

---

## Development

### Setup
```bash
git clone https://github.com/agkloop/advanced_caching.git
cd advanced_caching
uv sync
uv run pytest tests/ -v
```
---

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Add tests for new functionality
4. Ensure all tests pass (`pytest`)
5. Submit a pull request

---

## License

MIT License – See [LICENSE](LICENSE) for details.

---

## Changelog

### 0.1.0 (Initial Release)
- ✅ TTL Cache decorator
- ✅ SWR Cache decorator
- ✅ Background Cache with APScheduler
- ✅ InMemCache, RedisCache, HybridCache storage backends
- ✅ Full async/sync support
- ✅ Custom storage protocol
- ✅ Comprehensive test suite
- ✅ Benchmark suite

---

## Roadmap

- [ ] Distributed tracing/observability
- [ ] Metrics export (Prometheus)
- [ ] Cache warming strategies
- [ ] Serialization plugins (msgpack, protobuf)
- [ ] Redis cluster support
- [ ] DynamoDB backend example

---


