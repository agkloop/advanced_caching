"""
Advanced caching primitives: TTL decorators, SWR cache, and background loaders.

Expose storage backends, decorators, and scheduler utilities under `advanced_caching`.
"""

__version__ = "0.1.6"

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
    SWRCache,
    StaleWhileRevalidateCache,
    BackgroundCache,
    BGCache,
)

__all__ = [
    "__version__",
    "InMemCache",
    "RedisCache",
    "HybridCache",
    "CacheEntry",
    "CacheStorage",
    "validate_cache_storage",
    "PickleSerializer",
    "JsonSerializer",
    "TTLCache",
    "SWRCache",
    "StaleWhileRevalidateCache",
    "BackgroundCache",
    "BGCache",
]
