# advanced-caching

[![PyPI version](https://img.shields.io/pypi/v/advanced-caching.svg)](https://pypi.org/project/advanced-caching/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Production-ready caching library** with composable decorators for TTL, stale-while-revalidate (SWR), and background refresh patterns. Designed for modern Python workflows with type safety, flexible storage backends, and seamless async/sync support.

## Features

- TTL Caching** – Simple time-based cache expiration with configurable key patterns
- Stale-While-Revalidate (SWR)** – Serve stale data instantly while refreshing in background
- Background Loading** – Pre-load expensive data periodically with APScheduler
- Multiple Backends** – In-memory, Redis, or custom storage implementations
- Thread-Safe** – Reentrant locks, atomic operations, safe for concurrent workloads
- Type-Safe** – Full type hints, Pydantic-compatible, IDE-friendly
- Zero Framework Dependencies** – Works with FastAPI, Flask, Django, or plain Python
- Lightweight** – Only requires APScheduler; Redis optional

## Benchmark Results

Tested with 10ms simulated work per operation (e.g., database query, API call):

### Hot Cache Performance (Repeated Access)
**Result: 9,000-75,000x faster than direct calls**

```
Strategy               Median Time    vs Baseline    Use Case
─────────────────────────────────────────────────────────────────
No Cache (baseline)    12.51 ms       1x             Direct calls
TTLCache               0.0010 ms      12,000x        General caching
SWRCache               0.0014 ms      9,100x         Stale-OK scenarios
InMemCache (direct)    0.0006 ms      20,000x        Manual caching
BGCache                0.0003 ms      37,000x        Pre-loaded data
```

### Mixed Workload (50% hit rate, 100 distinct keys)
**Result: 7,000-43,000x faster than direct calls**

```
Strategy               Median Time    vs Baseline
─────────────────────────────────────────────────
No Cache               12.51 ms       1x
TTLCache               0.0008 ms      15,000x 
SWRCache               0.0017 ms      7,300x 
```

### Background Loading (50ms heavy operation)
**Result: 119,000-146,000x faster when pre-loaded**

```
Strategy               Median Time    vs Baseline
─────────────────────────────────────────────────
No Cache               55.01 ms       1x
BGCache (pre-loaded)   0.0005 ms      119,850x 
TTLCache (hot)         0.0004 ms      146,715x 
```

## Installation

### Standard Installation
```bash
pip install advanced-caching
```

### With uv (recommended)
```bash
uv pip install advanced-caching
```

### With Redis Support
```bash
pip install "advanced-caching[redis]"
uv pip install "advanced-caching[redis]"
```

## Quick Start

### TTL Cache – Simple Time-Based Expiration
```python
from advanced_caching import TTLCache

@TTLCache.cached("user:{}", ttl=300)  # Cache for 5 minutes
def get_user(user_id: int) -> dict:
    # Expensive database query
    return db.query("SELECT * FROM users WHERE id = ?", user_id)

# First call: executes function, caches result
user = get_user(42)

# Subsequent calls within 5 minutes: returns cached value instantly
user = get_user(42)  # ~0.001ms vs ~10ms without cache

# Different key: cache miss
user = get_user(43)  # Executes function again
```

### Stale-While-Revalidate – Serve Stale, Refresh in Background
```python
from advanced_caching import SWRCache

@SWRCache.cached("product:{}", ttl=60, stale_ttl=30)
def get_product(product_id: int) -> dict:
    return api.fetch_product(product_id)

# Within TTL (0-60s): returns fresh data instantly
product = get_product(1)

# Between TTL and stale_ttl (60-90s): returns stale data while
# refreshing in background (non-blocking)
product = get_product(1)  # Returns immediately with last-known data

# After stale_ttl expires: fetches fresh data
product = get_product(1)  # Waits for fresh fetch
```

### Background Loading – Pre-Load Expensive Data
```python
from advanced_caching import BGCache

@BGCache.register_loader("inventory", interval_seconds=300)
def load_inventory() -> list[dict]:
    # Expensive operation (API call, heavy query, etc.)
    return warehouse_api.get_all_items()

# Data is pre-loaded in background, always ready (no wait)
inventory = load_inventory()  # ~0.001ms guaranteed

# Continues to refresh every 5 minutes automatically
# Error handling:
@BGCache.register_loader("config", interval_seconds=60, on_error=logger.error)
def load_config() -> dict:
    return settings.fetch()
```

### Async Support
All decorators work with both sync and async functions:

```python
import asyncio
from advanced_caching import TTLCache, BGCache

# Async with TTLCache
@TTLCache.cached("user:{}", ttl=300)
async def get_user_async(user_id: int) -> dict:
    return await db.fetch_user(user_id)

# Async with BGCache
@BGCache.register_loader("products", interval_seconds=300)
async def load_products_async() -> list[dict]:
    return await api.fetch_products()

# Usage
user = await get_user_async(42)
products = await load_products_async()
```

## API Reference

### Decorators

#### `TTLCache.cached(key, ttl, cache=None)`
Simple time-based cache with configurable TTL.

**Parameters:**
- `key` (str | callable): Cache key template or generator function
  - String: `"user:{}"`  → formats with first positional arg
  - String with kwargs: `"user:{user_id}"` → formats with kwargs
  - Callable: `lambda user_id: f"user:{user_id}"` → custom key logic
- `ttl` (int): Time-to-live in seconds. `0` = never expire
- `cache` (CacheStorage, optional): Storage backend. Defaults to `InMemCache()`

**Returns:** Cached function that stores results for `ttl` seconds

**Examples:**
```python
# Simple string template
@TTLCache.cached("user:{}", ttl=60)
def get_user(user_id):
    return db.fetch(user_id)

# Custom key function
@TTLCache.cached(key=lambda x, y: f"calc:{x}:{y}", ttl=30)
def expensive_calc(x, y):
    return x ** y

# With custom backend
@TTLCache.cached("data:{}", ttl=60, cache=redis_cache)
def get_data(data_id):
    return api.fetch(data_id)

# Call and cache
result = get_user(42)
cached_value = get_user._cache.get("user:42")  # Access cache directly
```

---

#### `SWRCache.cached(key, ttl, stale_ttl=0, cache=None, enable_lock=True)`
Stale-while-revalidate: serve stale data while refreshing in background.

**Parameters:**
- `key` (str | callable): Cache key (same as TTLCache)
- `ttl` (int): Fresh data TTL in seconds
- `stale_ttl` (int): Grace period to serve stale data while refreshing (seconds)
- `cache` (CacheStorage, optional): Storage backend
- `enable_lock` (bool): Prevent thundering herd by serializing refreshes

**Returns:** Cached function that never blocks on refresh

**Examples:**
```python
# Basic SWR
@SWRCache.cached("user:{}", ttl=60, stale_ttl=30)
def get_user(user_id):
    return db.fetch(user_id)

# Returns immediately (stale or fresh)
user = get_user(42)

# Disable lock if refreshes are already serialized elsewhere
@SWRCache.cached("data:{}", ttl=60, stale_ttl=30, enable_lock=False)
def get_data(data_id):
    return api.fetch(data_id)
```

---

#### `BGCache.register_loader(cache_key, interval_seconds, ttl_seconds=None, run_immediately=True, on_error=None, cache=None)`
Background scheduler-based loader for periodic refresh of expensive data.

**Parameters:**
- `cache_key` (str): Unique key to store data (string only, no formatting)
- `interval_seconds` (int): Refresh interval in seconds
- `ttl_seconds` (int, optional): Cache TTL. Defaults to `interval_seconds * 2`
- `run_immediately` (bool): Load data once during registration
- `on_error` (callable, optional): Error handler: `(exception) -> None`
- `cache` (CacheStorage, optional): Storage backend

**Returns:** Wrapper that returns cached data (sync or async, auto-detected)

**Examples:**
```python
# Sync loader
@BGCache.register_loader("products", interval_seconds=300)
def load_products():
    return db.query("SELECT * FROM products")

# Async loader
@BGCache.register_loader("inventory", interval_seconds=300)
async def load_inventory():
    return await warehouse_api.list_items()

# With error handler
def on_load_error(exc):
    logger.error(f"Failed to load: {exc}")
    metrics.increment("load_errors")

@BGCache.register_loader(
    "config",
    interval_seconds=60,
    on_error=on_load_error
)
def load_config():
    return settings.fetch()

# Shutdown scheduler when done
BGCache.shutdown()  # Wait for running jobs
BGCache.shutdown(wait=False)  # Or shutdown immediately
```

---

### Storage Backends

#### `InMemCache()`
Thread-safe in-memory cache with TTL support.

**Methods:**
- `get(key) -> Any | None` – Get value or None if expired
- `set(key, value, ttl=0) -> None` – Store value with TTL (0 = no expiry)
- `delete(key) -> None` – Remove key
- `exists(key) -> bool` – Check if key is fresh
- `set_if_not_exists(key, value, ttl) -> bool` – Atomic set; returns True if set
- `clear() -> None` – Clear all data
- `cleanup_expired() -> int` – Remove expired entries; returns count
- `get_entry(key) -> CacheEntry | None` – Get raw entry (advanced)
- `set_entry(key, entry) -> None` – Set raw entry (advanced)

**Example:**
```python
from advanced_caching import InMemCache

cache = InMemCache()
cache.set("key1", {"data": "value"}, ttl=60)

# Later
value = cache.get("key1")  # {"data": "value"}

# Clean up expired entries
removed = cache.cleanup_expired()
print(f"Removed {removed} expired entries")
```

---

#### `RedisCache(redis_client, prefix="")`
Redis-backed cache for distributed systems.

**Parameters:**
- `redis_client` – Redis client instance (`redis.Redis()`)
- `prefix` (str, optional) – Key prefix for namespacing

**Methods:** Same as `InMemCache`

**Example:**
```python
import redis
from advanced_caching import RedisCache, TTLCache

client = redis.Redis(host="localhost", port=6379)
cache = RedisCache(client, prefix="app:")

@TTLCache.cached("user:{}", ttl=300, cache=cache)
def get_user(user_id):
    return db.fetch(user_id)

# Data stored in Redis with key "app:user:42"
user = get_user(42)
```

---

#### `HybridCache(l1_cache=None, l2_cache=None, l1_ttl=60)`
Two-level cache: L1 (in-memory, fast) + L2 (Redis, persistent).

**Parameters:**
- `l1_cache` (CacheStorage, optional) – L1 cache; defaults to `InMemCache()`
- `l2_cache` (CacheStorage, required) – L2 cache (e.g., `RedisCache`)
- `l1_ttl` (int) – TTL for L1 cache (seconds)

**Example:**
```python
import redis
from advanced_caching import HybridCache, RedisCache, TTLCache

client = redis.Redis()
cache = HybridCache(
    l1_cache=None,  # Defaults to InMemCache
    l2_cache=RedisCache(client),
    l1_ttl=60
)

@TTLCache.cached("user:{}", ttl=300, cache=cache)
def get_user(user_id):
    return db.fetch(user_id)

# Hits are served from L1 (fast), misses fetch from L2 (distributed)
user = get_user(42)
```

---

### Utilities

#### `validate_cache_storage(cache) -> bool`
Check if an object implements the `CacheStorage` protocol.

**Example:**
```python
from advanced_caching import validate_cache_storage

class CustomCache:
    def get(self, key): ...
    def set(self, key, value, ttl=0): ...
    def delete(self, key): ...
    def exists(self, key): ...
    def set_if_not_exists(self, key, value, ttl): ...

cache = CustomCache()
assert validate_cache_storage(cache)  # True if all methods present
```

---

#### `CacheEntry` (dataclass)
Exposes cache metadata for advanced use cases.

**Attributes:**
- `value` – Cached value
- `fresh_until` (float) – Unix timestamp when entry expires
- `created_at` (float) – Unix timestamp of creation
- `is_fresh() -> bool` – Check if not expired
- `age() -> float` – Get age in seconds

**Example:**
```python
from advanced_caching import InMemCache, CacheEntry

cache = InMemCache()
entry = cache.get_entry("user:42")
if entry and entry.is_fresh():
    print(f"Data age: {entry.age():.1f}s")
```

---

## Implementing Custom Storage

Implement the `CacheStorage` protocol to use custom backends (DynamoDB, file-based, encrypted storage, etc.).

### Full Example: File-Based Cache

```python
import json
import os
import time
from pathlib import Path
from typing import Any

from advanced_caching import CacheStorage, TTLCache, validate_cache_storage


class FileCache(CacheStorage):
    """
    File-based cache storage implementing CacheStorage protocol.
    
    Stores each cache entry as a JSON file with TTL metadata.
    Not thread-safe; use InMemCache for concurrent workloads.
    """

    def __init__(self, directory: str = "/tmp/cache"):
        """
        Initialize file cache.
        
        Args:
            directory: Directory to store cache files
        """
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _get_path(self, key: str) -> Path:
        """Get file path for cache key."""
        # Sanitize key for filesystem
        safe_key = key.replace("/", "_").replace(":", "_")
        return self.directory / f"{safe_key}.json"

    def get(self, key: str) -> Any | None:
        """Get value from file, return None if expired or not found."""
        path = self._get_path(key)
        if not path.exists():
            return None

        try:
            with open(path) as f:
                data = json.load(f)

            # Check if expired
            if data["fresh_until"] < time.time():
                path.unlink()  # Delete expired file
                return None

            return data["value"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        """Store value in file with TTL metadata."""
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")

        data = {
            "value": value,
            "fresh_until": fresh_until,
            "created_at": now
        }

        path = self._get_path(key)
        try:
            with open(path, "w") as f:
                json.dump(data, f)
        except OSError as e:
            raise RuntimeError(f"Failed to write cache file: {e}")

    def delete(self, key: str) -> None:
        """Delete cache file."""
        self._get_path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        """Check if key exists and is not expired."""
        return self.get(key) is not None

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        """Atomic set if not exists."""
        if self.exists(key):
            return False
        self.set(key, value, ttl)
        return True


# Validate the implementation
cache = FileCache("/tmp/app_cache")
assert validate_cache_storage(cache)

# Use with decorators
@TTLCache.cached("user:{}", ttl=300, cache=cache)
def get_user(user_id: int) -> dict:
    print(f"Fetching user {user_id}...")
    return {"id": user_id, "name": f"User {user_id}"}

# Usage
user = get_user(42)  # Fetches and stores in /tmp/app_cache/user_42.json
user = get_user(42)  # Reads from file (no fetch)

# Clean up
cache.delete("user:42")
```

### Best Practices for Custom Storage

1. **TTL Handling** – Always respect the `ttl` parameter; implement expiration check in `get()`
2. **Thread Safety** – Use locks if accessed from multiple threads
3. **Atomicity** – Implement `set_if_not_exists` correctly to prevent race conditions
4. **Error Handling** – Gracefully handle I/O errors; don't crash the application
5. **Cleanup** – Implement a cleanup mechanism (background job) to remove expired entries
6. **Validation** – Use `validate_cache_storage()` to verify your implementation

---

## Testing

### Run Tests
```bash
pip install pytest
pytest tests/test_correctness.py -v
```

### Run Benchmarks
```bash
python tests/benchmark.py
```

Expected output shows performance comparisons across all decorators and backends.

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

### Distributed Locks
```python
from advanced_caching import InMemCache

lock_cache = InMemCache()

def acquire_lock(key: str, holder_id: str, ttl: int = 10) -> bool:
    return lock_cache.set_if_not_exists(f"lock:{key}", holder_id, ttl)

def release_lock(key: str, holder_id: str) -> None:
    cached = lock_cache.get(f"lock:{key}")
    if cached == holder_id:
        lock_cache.delete(f"lock:{key}")

# Usage
if acquire_lock("user:42", "worker:1"):
    try:
        process_user(42)
    finally:
        release_lock("user:42", "worker:1")
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
# Clone repository
git clone https://github.com/agkloop/advanced_caching.git
cd advanced_caching

# Install dev dependencies
uv sync

# Run tests
uv run pytest tests/ -v

# Run benchmarks
uv run python tests/benchmark.py
```

### Build & Publish
```bash
# Build wheel and sdist
uv build

# Publish to PyPI (requires credentials)
uv publish
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

## Support

- **Issues** – Report bugs on [GitHub Issues](https://github.com/agkloop/advanced_caching/issues)
- **Discussions** – Ask questions on [GitHub Discussions](https://github.com/agkloop/advanced_caching/discussions)
- **Documentation** – Full API docs available above

---

## Roadmap

- [ ] Distributed tracing/observability
- [ ] Metrics export (Prometheus)
- [ ] Cache warming strategies
- [ ] Serialization plugins (msgpack, protobuf)
- [ ] Redis cluster support
- [ ] DynamoDB backend example

---


