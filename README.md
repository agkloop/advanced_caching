# advanced-caching

[![PyPI version](https://img.shields.io/pypi/v/advanced-caching.svg)](https://pypi.org/project/advanced-caching/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Production-ready caching library** with decorators for TTL, stale-while-revalidate (SWR), and background refresh. Type-safe, fast, and framework-agnostic.

## Quick Links

- [Installation](#installation) – Get started in 30 seconds
- [Quick Examples](#quick-start) – Copy-paste ready code
- [API Reference](#api-reference) – Full decorator & backend docs
- [Storage & Redis](#storage--redis) – Redis/Hybrid/custom storage examples
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
# with Redis support (uv)
uv pip install "advanced-caching[redis]"
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
Full benchmarks available in `tests/benchmark.py`.

Step-by-step benchmarking + profiling guide: `docs/benchmarking-and-profiling.md`.

Storage & Redis usage is documented below.

## API Reference

### Key templates & custom keys

All caching decorators share the same key concept:

- `key: str` – String template or literal
- `key: Callable[..., str]` – Function that returns a string key

Supported patterns:

1. **Positional placeholder** – first positional argument:

   ```python
   @TTLCache.cached("user:{}", ttl=60)
   def get_user(user_id: int):
       ...

   get_user(42)  # key -> "user:42"
   ```

2. **Named placeholder** – keyword arguments by name:

   ```python
   @TTLCache.cached("user:{user_id}", ttl=60)
   def get_user(*, user_id: int):
       ...

   get_user(user_id=42)  # key -> "user:42"
   ```

3. **Named with extra kwargs** – only the named part is used for the key:

   ```python
   @SWRCache.cached("i18n:{lang}", ttl=60, stale_ttl=30)
   def load_i18n(lang: str, region: str | None = None):
       ...

   load_i18n(lang="en", region="US")  # key -> "i18n:en"
   ```

4. **Default arguments + robust key lambda** – recommended for complex/default cases:

   ```python
   @SWRCache.cached(
       key=lambda *a, **k: f"i18n:all:{k.get('lang', a[0] if a else 'en')}",
       ttl=60,
       stale_ttl=30,
   )
   def load_all(lang: str = "en") -> dict:
       print(f"Loading i18n for {lang}")
       return {"hello": f"Hello in {lang}"}

   load_all()           # key -> "i18n:all:en"
   load_all("en")      # key -> "i18n:all:en"
   load_all(lang="en") # key -> "i18n:all:en"
   # Body runs once, subsequent calls are cached
   ```

---

### TTLCache.cached(key, ttl, cache=None)
Simple time-based cache with configurable TTL.

**Signature:**
```python
TTLCache.cached(
    key: str | Callable[..., str],
    ttl: int,
    cache: CacheStorage | Callable[[], CacheStorage] | None = None,
) -> Callable
```

**Parameters:**
- `key` (str | callable): Cache key template or generator function
- `ttl` (int): Time-to-live in seconds
- `cache` (CacheStorage): Optional custom backend (defaults to InMemCache)

**Examples:**

Positional key:
```python
@TTLCache.cached("user:{}", ttl=300)
def get_user(user_id: int):
    return db.fetch(user_id)

get_user(42)  # key -> "user:42"
```

Named key:
```python
@TTLCache.cached("user:{user_id}", ttl=300)
def get_user(*, user_id: int):
    return db.fetch(user_id)

get_user(user_id=42)  # key -> "user:42"
```

Custom key function:
```python
@TTLCache.cached(key=lambda *a, **k: f"user:{k.get('user_id', a[0])}", ttl=300)
def get_user(user_id: int = 0):
    return db.fetch(user_id)
```

---

### SWRCache.cached(key, ttl, stale_ttl=0, cache=None, enable_lock=True)
Serve stale data instantly while refreshing in background.

**Signature:**
```python
SWRCache.cached(
    key: str | Callable[..., str],
    ttl: int,
    stale_ttl: int = 0,
    cache: CacheStorage | Callable[[], CacheStorage] | None = None,
    enable_lock: bool = True,
) -> Callable
```

**Parameters:**
- `key` (str | callable): Cache key (same patterns as TTLCache)
- `ttl` (int): Fresh data TTL in seconds
- `stale_ttl` (int): Grace period to serve stale data while refreshing
- `cache` (CacheStorage): Optional custom backend
- `enable_lock` (bool): Prevent thundering herd (default: True)

**Examples:**

Basic SWR with positional key:
```python
@SWRCache.cached("product:{}", ttl=60, stale_ttl=30)
def get_product(product_id: int):
    return api.fetch_product(product_id)

get_product(1)  # key -> "product:1"
```

Named key with kwargs:
```python
@SWRCache.cached("i18n:{lang}", ttl=60, stale_ttl=30)
def load_i18n(*, lang: str = "en") -> dict:
    return {"hello": f"Hello in {lang}"}

load_i18n(lang="en")  # key -> "i18n:en"
```

Default arg + key lambda (robust):
```python
@SWRCache.cached(
    key=lambda *a, **k: f"i18n:all:{k.get('lang', a[0] if a else 'en')}",
    ttl=60,
    stale_ttl=30,
)
def load_all(lang: str = "en") -> dict:
    return {"hello": f"Hello in {lang}"}
```

---

### BGCache.register_loader(key, interval_seconds, ttl=None, run_immediately=True, on_error=None, cache=None)
Pre-load expensive data with periodic refresh.

**Signature:**
```python
BGCache.register_loader(
    key: str,
    interval_seconds: int,
    ttl: int | None = None,
    run_immediately: bool = True,
    on_error: Callable[[Exception], None] | None = None,
    cache: CacheStorage | Callable[[], CacheStorage] | None = None,
) -> Callable
```

**Parameters:**
- `key` (str): Unique cache key (no formatting, fixed string)
- `interval_seconds` (int): Refresh interval in seconds
- `ttl` (int | None): Cache TTL (defaults to 2 × interval_seconds when None)
- `run_immediately` (bool): Load once at registration (default: True)
- `on_error` (callable): Error handler function `(Exception) -> None`
- `cache` (CacheStorage): Optional custom backend

**Examples:**

Sync loader:
```python
from advanced_caching import BGCache

@BGCache.register_loader(key="inventory", interval_seconds=300, ttl=900)
def load_inventory() -> list[dict]:
    return warehouse_api.get_all_items()

# Later
items = load_inventory()  # instant access to cached data
```

Async loader:
```python
@BGCache.register_loader(key="products", interval_seconds=300, ttl=900)
async def load_products() -> list[dict]:
    return await api.fetch_products()

products = await load_products()  # returns cached list
```

With error handling:
```python
errors: list[Exception] = []

def on_error(exc: Exception) -> None:
    errors.append(exc)

@BGCache.register_loader(
    key="unstable",
    interval_seconds=60,
    run_immediately=True,
    on_error=on_error,
)
def maybe_fails() -> dict:
    raise RuntimeError("boom")

# errors list will contain the exception from background job
```

Shutdown scheduler when done:
```python
BGCache.shutdown(wait=True)
```

---

### Storage Backends

## Storage & Redis

### Install (uv)

```bash
uv pip install advanced-caching
uv pip install "advanced-caching[redis]"  # for RedisCache / HybridCache
```

### How storage is chosen

- If you don’t pass `cache=...`, each decorated function lazily creates its own `InMemCache` instance.
- You can pass either a cache instance (`cache=my_cache`) or a cache factory (`cache=lambda: my_cache`).

### Share one storage instance

```python
from advanced_caching import InMemCache, TTLCache

shared = InMemCache()

@TTLCache.cached("user:{}", ttl=60, cache=shared)
def get_user(user_id: int) -> dict:
    return {"id": user_id}

@TTLCache.cached("org:{}", ttl=60, cache=shared)
def get_org(org_id: int) -> dict:
    return {"id": org_id}
```

### Use RedisCache (distributed)

`RedisCache` stores values in Redis using `pickle`.

```python
import redis
from advanced_caching import RedisCache, TTLCache

client = redis.Redis(host="localhost", port=6379)
cache = RedisCache(client, prefix="app:")

@TTLCache.cached("user:{}", ttl=300, cache=cache)
def get_user(user_id: int) -> dict:
    return {"id": user_id}
```

### Use SWRCache with RedisCache (recommended)

`SWRCache` uses `get_entry`/`set_entry` so it can store freshness metadata.

```python
import redis
from advanced_caching import RedisCache, SWRCache

client = redis.Redis(host="localhost", port=6379)
cache = RedisCache(client, prefix="products:")

@SWRCache.cached("product:{}", ttl=60, stale_ttl=30, cache=cache)
def get_product(product_id: int) -> dict:
    return {"id": product_id}
```

### Use HybridCache (L1 memory + L2 Redis)

`HybridCache` is a two-level cache:
- **L1**: fast in-memory (`InMemCache`)
- **L2**: Redis-backed (`RedisCache`)

Reads go to L1 first; on L1 miss it tries L2; on L2 hit it warms L1.

```python
import redis
from advanced_caching import HybridCache, InMemCache, RedisCache, TTLCache

client = redis.Redis(host="localhost", port=6379)

hybrid = HybridCache(
    l1_cache=InMemCache(),
    l2_cache=RedisCache(client, prefix="app:"),
    l1_ttl=60,
)

@TTLCache.cached("user:{}", ttl=300, cache=hybrid)
def get_user(user_id: int) -> dict:
    return {"id": user_id}
```

Notes:
- `ttl` on the decorator controls how long values are considered valid.
- `l1_ttl` controls how long HybridCache keeps values in memory after an L2 hit.

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

### File-based example

```python
import json
import time
from pathlib import Path
from advanced_caching import CacheEntry, CacheStorage, TTLCache, validate_cache_storage


class FileCache(CacheStorage):
    def __init__(self, directory: str = "/tmp/cache"):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def _get_path(self, key: str) -> Path:
        safe_key = key.replace("/", "_").replace(":", "_")
        return self.directory / f"{safe_key}.json"

    def get_entry(self, key: str) -> CacheEntry | None:
        path = self._get_path(key)
        if not path.exists():
            return None
        try:
            with open(path) as f:
                data = json.load(f)
            return CacheEntry(
                value=data["value"],
                fresh_until=float(data["fresh_until"]),
                created_at=float(data["created_at"]),
            )
        except Exception:
            return None

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        now = time.time()
        if ttl is not None:
            fresh_until = now + ttl if ttl > 0 else float("inf")
            entry = CacheEntry(value=entry.value, fresh_until=fresh_until, created_at=now)
        with open(self._get_path(key), "w") as f:
            json.dump(
                {"value": entry.value, "fresh_until": entry.fresh_until, "created_at": entry.created_at},
                f,
            )

    def get(self, key: str):
        entry = self.get_entry(key)
        if entry is None:
            return None
        if not entry.is_fresh():
            self.delete(key)
            return None
        return entry.value

    def set(self, key: str, value, ttl: int = 0) -> None:
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")
        self.set_entry(key, CacheEntry(value=value, fresh_until=fresh_until, created_at=now))

    def delete(self, key: str) -> None:
        self._get_path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self.get(key) is not None

    def set_if_not_exists(self, key: str, value, ttl: int) -> bool:
        if self.exists(key):
            return False
        self.set(key, value, ttl)
        return True


cache = FileCache("/tmp/app_cache")
assert validate_cache_storage(cache)

@TTLCache.cached("user:{}", ttl=300, cache=cache)
def get_user(user_id: int):
    return {"id": user_id}
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
uv run pytest tests/test_correctness.py -v
```

### Run Benchmarks
```bash
uv run python tests/benchmark.py
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
4. Ensure all tests pass (`uv run pytest`)
5. Submit a pull request

---

## License

MIT License – See [LICENSE](LICENSE) for details.
