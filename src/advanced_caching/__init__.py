"""
Advanced caching primitives: TTL decorators, SWR cache, and background loaders.

Expose storage backends, decorators, and scheduler utilities under `advanced_caching`.
"""

from .storage import (
    InMemCache,
    RedisCache,
    HybridCache,
    CacheEntry,
    CacheStorage,
    validate_cache_storage,
)
from .decorators import (
    TTLCache,
    SWRCache,
    StaleWhileRevalidateCache,
    BackgroundCache,
    BGCache,
)

__all__ = [
    "InMemCache",
    "RedisCache",
    "HybridCache",
    "CacheEntry",
    "CacheStorage",
    "validate_cache_storage",
    "TTLCache",
    "SWRCache",
    "StaleWhileRevalidateCache",
    "BackgroundCache",
    "BGCache",
]
