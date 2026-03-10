"""advanced_caching — fast, clean, composable caching for Python.

Quick start::

    from advanced_caching import cache, bg

    @cache(60, key="user:{user_id}")
    async def get_user(user_id: int) -> dict:
        return await db.fetch(user_id)

    @cache(60, stale=30, key="feed:{}")
    async def get_feed(user_id: int) -> list:
        return await db.fetch_feed(user_id)

    @bg(300, key="app_config")
    async def load_config() -> dict:
        return await fetch_remote_config()

Custom stores::

    from advanced_caching import cache, RedisCache, ChainCache, InMemCache
    from advanced_caching import serializers
    import redis

    redis_store = RedisCache(
        redis.from_url("redis://localhost"),
        prefix="myapp:",
        serializer=serializers.json,
    )
    tiered = ChainCache.build(InMemCache(), redis_store, ttls=[60, 3600])

    @cache(3600, key="prices:{symbol}", store=tiered)
    async def get_price(symbol: str) -> float: ...

Metrics::

    from advanced_caching import cache, InMemoryMetrics

    metrics = InMemoryMetrics()

    @cache(60, key="user:{}", metrics=metrics)
    async def get_user(user_id): ...

    print(metrics.get_stats())
"""

__version__ = "0.4.0"

from ._cache import cache, bg
from . import serializers
from .serializers import (
    Serializer,
    PickleSerializer,
    JsonSerializer,
    MsgpackSerializer,
    protobuf,
    pack_entry,
    unpack_entry,
)
from .metrics import InMemoryMetrics
from .storage import (
    CacheEntry,
    CacheStorage,
    InstrumentedStorage,
    validate_cache_storage,
    InMemCache,
    RedisCache,
    HybridCache,
    ChainCache,
    LocalFileCache,
    S3Cache,
    GCSCache,
)

__all__ = [
    # Decorators
    "cache",
    "bg",
    # Serializers
    "serializers",
    "Serializer",
    "PickleSerializer",
    "JsonSerializer",
    "MsgpackSerializer",
    "protobuf",
    "pack_entry",
    "unpack_entry",
    # Metrics
    "InMemoryMetrics",
    # Storage
    "CacheEntry",
    "CacheStorage",
    "InstrumentedStorage",
    "validate_cache_storage",
    "InMemCache",
    "RedisCache",
    "HybridCache",
    "ChainCache",
    "LocalFileCache",
    "S3Cache",
    "GCSCache",
]
