"""
Cache decorators for function result caching.

Provides:
- TTLCache: Simple TTL-based caching
- SWRCache: Stale-while-revalidate pattern
- BGCache: Background scheduler-based loading with APScheduler
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable, TypeVar, ClassVar

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .storage import InMemCache, CacheEntry, CacheStorage

logger = logging.getLogger(__name__)

T = TypeVar("T")


# ============================================================================
# TTLCache - Simple TTL-based caching decorator
# ============================================================================


class SimpleTTLCache:
    """
    Simple TTL cache decorator (singleton pattern).
    Each decorated function gets its own cache instance.

    Example:
        @TTLCache.cached("user:{}", ttl=60)
        def get_user(user_id):
            return db.fetch_user(user_id)

        # With custom cache backend
        @TTLCache.cached("user:{}", ttl=60, cache=redis_cache)
        def get_user(user_id):
            return db.fetch_user(user_id)
    """

    @classmethod
    def cached(
        cls, key: str | Callable[..., str], ttl: int, cache: CacheStorage | None = None
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """
        Cache decorator with TTL.

        Args:
            key: Cache key template (e.g., "user:{}") or generator function
            ttl: Time-to-live in seconds
            cache: Optional cache backend (defaults to InMemCache)

        Example:
            @TTLCache.cached("user:{}", ttl=300)
            def get_user(user_id):
                return db.fetch_user(user_id)

            # With key function
            @TTLCache.cached(key=lambda x: f"calc:{x}", ttl=60)
            def calculate(x):
                return x * 2
        """
        # Each decorated function gets its own cache instance
        function_cache = cache if cache is not None else InMemCache()

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args, **kwargs) -> T:
                # Generate cache key
                if callable(key):
                    cache_key = key(*args, **kwargs)
                else:
                    # Simple format string with first arg
                    if args:
                        cache_key = key.format(args[0]) if "{" in key else key
                    else:
                        cache_key = key.format(**kwargs) if "{" in key else key

                # Try cache first
                cached_value = function_cache.get(cache_key)
                if cached_value is not None:
                    return cached_value

                # Cache miss - call function
                result = func(*args, **kwargs)
                function_cache.set(cache_key, result, ttl)
                return result

            # Store cache reference for testing/debugging
            wrapper.__wrapped__ = func  # type: ignore
            wrapper.__name__ = func.__name__  # type: ignore
            wrapper.__doc__ = func.__doc__  # type: ignore
            wrapper._cache = function_cache  # type: ignore

            return wrapper

        return decorator


# Alias for easier import
TTLCache = SimpleTTLCache


# ============================================================================
# SWRCache - Stale-While-Revalidate pattern
# ============================================================================


class StaleWhileRevalidateCache:
    """
    SWR cache with background refresh - composable with any cache backend.
    Serves stale data while refreshing in background (non-blocking).

    Example:
        @SWRCache.cached("product:{}", ttl=60, stale_ttl=30)
        def get_product(product_id: int):
            return db.fetch_product(product_id)

        # With Redis
        @SWRCache.cached("product:{}", ttl=60, stale_ttl=30, cache=redis_cache)
        def get_product(product_id: int):
            return db.fetch_product(product_id)
    """

    @classmethod
    def cached(
        cls,
        key: str | Callable[..., str],
        ttl: int,
        stale_ttl: int = 0,
        cache: CacheStorage | None = None,
        enable_lock: bool = True,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """
        SWR cache decorator.

        Args:
            key: Cache key template or generator function.
            ttl: Fresh TTL in seconds.
            stale_ttl: Additional time to serve stale data while refreshing.
            cache: Optional cache backend (InMemCache, RedisCache, etc.).
            enable_lock: Whether to use locking to prevent thundering herd.

        Example:
            @SWRCache.cached("user:{}", ttl=60, stale_ttl=30)
            def get_user(user_id: int):
                return db.query("SELECT * FROM users WHERE id = ?", user_id)

            # With Redis
            @SWRCache.cached("user:{}", ttl=60, stale_ttl=30, cache=redis_cache)
            def get_user(user_id: int):
                return db.query("SELECT * FROM users WHERE id = ?", user_id)
        """
        # Each decorated function gets its own cache instance
        function_cache = cache if cache is not None else InMemCache()

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args, **kwargs) -> T:
                # Generate cache key
                if callable(key):
                    cache_key = key(*args, **kwargs)
                else:
                    if args:
                        cache_key = key.format(args[0]) if "{" in key else key
                    else:
                        cache_key = key.format(**kwargs) if "{" in key else key

                # Try to get from cache
                entry = function_cache.get_entry(cache_key)

                if entry is None:
                    # Cache miss - fetch now
                    logger.debug(f"Cache MISS: {cache_key}")
                    result = func(*args, **kwargs)
                    now = time.time()
                    cache_entry = CacheEntry(
                        value=result, fresh_until=now + ttl, created_at=now
                    )
                    function_cache.set_entry(cache_key, cache_entry)
                    return result

                # Check if fresh (within TTL)
                if entry.is_fresh():
                    logger.debug(f"Cache HIT (fresh): {cache_key}")
                    return entry.value

                # Stale - check if still within stale period
                age = entry.age()
                if age > (ttl + stale_ttl):
                    # Too stale, fetch now
                    logger.debug(f"Cache HIT (too stale): {cache_key}, age={age:.1f}s")
                    result = func(*args, **kwargs)
                    now = time.time()
                    cache_entry = CacheEntry(
                        value=result, fresh_until=now + ttl, created_at=now
                    )
                    function_cache.set_entry(cache_key, cache_entry)
                    return result

                # Stale but within grace period - return stale and refresh in background
                logger.debug(
                    f"Cache HIT (stale): {cache_key}, refreshing in background"
                )

                # Try to acquire refresh lock
                lock_key = f"{cache_key}:refresh_lock"
                if enable_lock:
                    acquired = function_cache.set_if_not_exists(
                        lock_key, "1", stale_ttl or 10
                    )
                    if not acquired:
                        return entry.value

                # Refresh in background thread
                def refresh_job():
                    try:
                        new_value = func(*args, **kwargs)
                        now = time.time()
                        cache_entry = CacheEntry(
                            value=new_value, fresh_until=now + ttl, created_at=now
                        )
                        function_cache.set_entry(cache_key, cache_entry)
                        logger.debug(f"Background refresh complete: {cache_key}")
                    except Exception as e:
                        logger.error(f"Background refresh failed for {cache_key}: {e}")

                thread = threading.Thread(target=refresh_job, daemon=True)
                thread.start()

                return entry.value

            wrapper.__wrapped__ = func  # type: ignore
            wrapper.__name__ = func.__name__  # type: ignore
            wrapper.__doc__ = func.__doc__  # type: ignore
            wrapper._cache = function_cache  # type: ignore
            return wrapper

        return decorator


# Alias for shorter usage
SWRCache = StaleWhileRevalidateCache


# ============================================================================
# Shared Scheduler - Singleton for all background jobs
# ============================================================================


class _SharedScheduler:
    """
    Shared BackgroundScheduler instance - singleton for all background jobs.
    Ensures only one scheduler runs for all registered loaders.
    """

    _scheduler: ClassVar[BackgroundScheduler | None] = None
    _lock: ClassVar[threading.RLock] = threading.RLock()
    _started: ClassVar[bool] = False

    @classmethod
    def get_scheduler(cls) -> BackgroundScheduler:
        """Get or create the shared background scheduler instance."""
        with cls._lock:
            if cls._scheduler is None:
                cls._scheduler = BackgroundScheduler(daemon=True)
            assert cls._scheduler is not None  # Type narrowing for IDE
        return cls._scheduler

    @classmethod
    def start(cls) -> None:
        """Start the shared background scheduler."""
        with cls._lock:
            if not cls._started:
                cls.get_scheduler().start()
                cls._started = True
                logger.info("Shared BackgroundScheduler started")

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        """Stop the shared background scheduler."""
        with cls._lock:
            if cls._started and cls._scheduler is not None:
                cls._scheduler.shutdown(wait=wait)
                cls._started = False
                cls._scheduler = None
                logger.info("Shared BackgroundScheduler stopped")


# ============================================================================
# BGCache - Background cache loader decorator
# ============================================================================


class BackgroundCache:
    """
    Background cache with BackgroundScheduler for periodic data loading.
    All instances share ONE BackgroundScheduler, but each has its own cache storage.
    Works with both sync and async functions.

    Example:
        # Async function
        @BGCache.register_loader("categories", interval_seconds=300)
        async def load_categories():
            return await db.query("SELECT * FROM categories")

        # Sync function
        @BGCache.register_loader("config", interval_seconds=300)
        def load_config():
            return {"key": "value"}

        # With custom cache backend
        @BGCache.register_loader("products", interval_seconds=300, cache=redis_cache)
        def load_products():
            return fetch_products_from_db()
    """

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        """
        Stop the shared BackgroundScheduler.

        Args:
            wait: Whether to wait for running jobs to complete
        """
        _SharedScheduler.shutdown(wait)

    @classmethod
    def register_loader(
        cls,
        cache_key: str,
        interval_seconds: int,
        ttl_seconds: int | None = None,
        run_immediately: bool = True,
        on_error: Callable[[Exception], None] | None = None,
        cache: CacheStorage | None = None,
    ) -> Callable[[Callable[[], T]], Callable[[], T]]:
        """
        Decorator to register a background data loader.
        Each loader gets its own cache instance (not shared).
        All loaders share ONE BackgroundScheduler instance.

        Args:
            cache_key: Unique key to store the loaded data
            interval_seconds: How often to refresh the data (in seconds)
            ttl_seconds: Cache TTL (defaults to interval_seconds * 2)
            run_immediately: Whether to load data immediately on registration
            on_error: Optional error handler callback
            cache: Optional cache backend (InMemCache, RedisCache, etc.)

        Returns:
            Decorated function that returns cached data

        Example:
            @BGCache.register_loader("products", interval_seconds=300)
            async def load_products():
                return await db.query("SELECT * FROM products")

            # Call the function to get cached data
            products = await load_products()

            # Sync functions also supported
            @BGCache.register_loader("config", interval_seconds=300)
            def load_config():
                return {"key": "value"}

            config = load_config()
        """
        if ttl_seconds is None:
            ttl_seconds = interval_seconds * 2

        # Create a dedicated cache instance for this loader
        loader_cache = cache if cache is not None else InMemCache()

        def decorator(loader_func: Callable[[], T]) -> Callable[[], T]:
            # Detect if function is async
            is_async = asyncio.iscoroutinefunction(loader_func)

            # Create wrapper that loads and caches
            def refresh_job():
                """Job that runs periodically to refresh the cache."""
                try:
                    logger.debug(f"Refreshing cache key: {cache_key}")
                    start = time.time()

                    # Call function (async or sync)
                    if is_async:
                        # Run async function in new event loop
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            data = loop.run_until_complete(loader_func())
                        finally:
                            loop.close()
                    else:
                        data = loader_func()

                    duration = time.time() - start

                    loader_cache.set(cache_key, data, ttl_seconds)
                    logger.info(
                        f"Refreshed {cache_key} successfully in {duration:.3f}s"
                    )
                except Exception as e:
                    logger.error(f"Failed to refresh {cache_key}: {e}", exc_info=True)
                    if on_error:
                        try:
                            on_error(e)
                        except Exception as err:
                            logger.error(f"Error handler failed: {err}")

            # Get shared scheduler
            scheduler = _SharedScheduler.get_scheduler()

            # Run immediately if requested
            if run_immediately:
                refresh_job()

            # Schedule periodic refresh
            scheduler.add_job(
                refresh_job,
                trigger=IntervalTrigger(seconds=interval_seconds),
                id=cache_key,
                replace_existing=True,
            )

            # Start scheduler if not already started
            _SharedScheduler.start()

            # Return a wrapper that gets from cache
            if is_async:

                async def async_wrapper() -> T:
                    """Get cached data or call loader if not available."""
                    value = loader_cache.get(cache_key)
                    if value is not None:
                        return value
                    # If not in cache yet, call loader directly
                    return await loader_func()

                async_wrapper.__wrapped__ = loader_func  # type: ignore
                async_wrapper.__name__ = loader_func.__name__  # type: ignore
                async_wrapper.__doc__ = loader_func.__doc__  # type: ignore
                async_wrapper._cache = loader_cache  # type: ignore
                async_wrapper._cache_key = cache_key  # type: ignore

                return async_wrapper  # type: ignore
            else:

                def sync_wrapper() -> T:
                    """Get cached data or call loader if not available."""
                    value = loader_cache.get(cache_key)
                    if value is not None:
                        return value
                    # If not in cache yet, call loader directly
                    return loader_func()

                sync_wrapper.__wrapped__ = loader_func  # type: ignore
                sync_wrapper.__name__ = loader_func.__name__  # type: ignore
                sync_wrapper.__doc__ = loader_func.__doc__  # type: ignore
                sync_wrapper._cache = loader_cache  # type: ignore
                sync_wrapper._cache_key = cache_key  # type: ignore

                return sync_wrapper  # type: ignore

        return decorator


# Alias for shorter usage
BGCache = BackgroundCache
