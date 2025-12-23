"""
Advanced caching primitives: TTL decorators, SWR cache, and background loaders.

Expose storage backends, decorators, and scheduler utilities under `advanced_caching`.
"""

__version__ = "0.2.0"

from .storage import (
    InMemCache,
    RedisCache,
    HybridCache,
    CacheEntry,
    CacheStorage,
    validate_cache_storage,
    PickleSerializer,
    JsonSerializer,
)
from .decorators import (
    TTLCache,
    AsyncTTLCache,
    SWRCache,
    AsyncStaleWhileRevalidateCache,
    BGCache,
    AsyncBackgroundCache,
)

__all__ = [
    "InMemCache",
    "RedisCache",
    "HybridCache",
    "CacheEntry",
    "CacheStorage",
    "validate_cache_storage",
    "PickleSerializer",
    "JsonSerializer",
    "TTLCache",
    "AsyncTTLCache",
    "SWRCache",
    "AsyncStaleWhileRevalidateCache",
    "BGCache",
    "AsyncBackgroundCache",
]
