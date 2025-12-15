"""
Cache decorators for function result caching.

Provides:
- TTLCache: Simple TTL-based caching
- SWRCache: Stale-while-revalidate pattern
- BGCache: Background scheduler-based loading with APScheduler
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, TypeVar, ClassVar

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .storage import InMemCache, CacheEntry, CacheStorage

T = TypeVar("T")

# Minimal logger used only for error reporting (no debug/info on hot paths)
logger = logging.getLogger(__name__)


_SWR_EXECUTOR: ThreadPoolExecutor | None = None
_SWR_EXECUTOR_LOCK = threading.Lock()


def _get_swr_executor() -> ThreadPoolExecutor:
    global _SWR_EXECUTOR
    if _SWR_EXECUTOR is None:
        with _SWR_EXECUTOR_LOCK:
            if _SWR_EXECUTOR is None:
                max_workers = min(32, (os.cpu_count() or 1) * 4)
                _SWR_EXECUTOR = ThreadPoolExecutor(
                    max_workers=max_workers, thread_name_prefix="advanced_caching_swr"
                )

                def _shutdown() -> None:
                    try:
                        _SWR_EXECUTOR.shutdown(wait=False, cancel_futures=True)  # type: ignore[union-attr]
                    except Exception:
                        pass

                atexit.register(_shutdown)
    return _SWR_EXECUTOR


# ============================================================================
# TTLCache - Simple TTL-based caching decorator
# ============================================================================


class SimpleTTLCache:
    """
    Simple TTL cache decorator (singleton pattern).
    Each decorated function gets its own cache instance.

    Key templates (high-performance, simple):
    - Positional placeholder: "user:{}" → first positional arg
    - Named placeholder: "user:{user_id}" → keyword arg `user_id`
    - Custom function: key=lambda *a, **k: ...

    Examples:
        @TTLCache.cached("user:{}", ttl=60)
        def get_user(user_id):
            return db.fetch_user(user_id)

        @TTLCache.cached("user:{user_id}", ttl=60)
        def get_user(*, user_id):
            return db.fetch_user(user_id)

        @TTLCache.cached(key=lambda *, lang="en": f"i18n:{lang}", ttl=60)
        def load_i18n(lang: str = "en"):
            ...
    """

    @classmethod
    def cached(
        cls,
        key: str | Callable[..., str],
        ttl: int,
        cache: CacheStorage | Callable[[], CacheStorage] | None = None,
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
        cache_factory: Callable[[], CacheStorage]
        if cache is None:
            cache_factory = InMemCache
        elif callable(cache):
            cache_factory = cache  # type: ignore[assignment]
        else:
            cache_instance = cache

            def cache_factory() -> CacheStorage:
                return cache_instance

        function_cache: CacheStorage | None = None
        cache_lock = threading.Lock()

        def get_cache() -> CacheStorage:
            nonlocal function_cache
            if function_cache is None:
                with cache_lock:
                    if function_cache is None:
                        function_cache = cache_factory()
            return function_cache

        # Precompute key builder to reduce per-call branching
        if callable(key):
            key_fn: Callable[..., str] = key  # type: ignore[assignment]
        else:
            template = key

            # Fast path for common templates like "prefix:{}" (single positional placeholder).
            if "{" not in template:

                def key_fn(*args, **kwargs) -> str:
                    return template

            elif (
                template.count("{}") == 1
                and template.count("{") == 1
                and template.count("}") == 1
            ):
                prefix, suffix = template.split("{}", 1)

                def key_fn(*args, **kwargs) -> str:
                    if args:
                        return prefix + str(args[0]) + suffix
                    if kwargs:
                        if len(kwargs) == 1:
                            return prefix + str(next(iter(kwargs.values()))) + suffix
                        return template
                    return template

            else:

                def key_fn(*args, **kwargs) -> str:
                    if args:
                        try:
                            return template.format(args[0])
                        except Exception:
                            return template
                    if kwargs:
                        try:
                            return template.format(**kwargs)
                        except Exception:
                            # Attempt single-kwarg positional fallback
                            if len(kwargs) == 1:
                                try:
                                    return template.format(next(iter(kwargs.values())))
                                except Exception:
                                    return template
                            return template
                    return template

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args, **kwargs) -> T:
                # If ttl is 0 or negative, disable caching and call through
                if ttl <= 0:
                    return func(*args, **kwargs)
                cache_key = key_fn(*args, **kwargs)

                cache_obj = get_cache()

                # Try cache first
                cached_value = cache_obj.get(cache_key)
                if cached_value is not None:
                    return cached_value

                # Cache miss - call function
                result = func(*args, **kwargs)
                cache_obj.set(cache_key, result, ttl)
                return result

            # Store cache reference for testing/debugging
            wrapper.__wrapped__ = func  # type: ignore
            wrapper.__name__ = func.__name__  # type: ignore
            wrapper.__doc__ = func.__doc__  # type: ignore
            wrapper._cache = get_cache()  # type: ignore

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
        cache: CacheStorage | Callable[[], CacheStorage] | None = None,
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
        cache_factory: Callable[[], CacheStorage]
        if cache is None:
            cache_factory = InMemCache
        elif callable(cache):
            cache_factory = cache  # type: ignore[assignment]
        else:
            cache_instance = cache

            def cache_factory() -> CacheStorage:
                return cache_instance

        function_cache: CacheStorage | None = None
        cache_lock = threading.Lock()

        def get_cache() -> CacheStorage:
            nonlocal function_cache
            if function_cache is None:
                with cache_lock:
                    if function_cache is None:
                        function_cache = cache_factory()
            return function_cache

        # Precompute key builder to reduce per-call branching
        if callable(key):
            key_fn: Callable[..., str] = key  # type: ignore[assignment]
        else:
            template = key

            # Fast path for common templates like "prefix:{}" (single positional placeholder).
            if "{" not in template:

                def key_fn(*args, **kwargs) -> str:
                    return template

            elif (
                template.count("{}") == 1
                and template.count("{") == 1
                and template.count("}") == 1
            ):
                prefix, suffix = template.split("{}", 1)

                def key_fn(*args, **kwargs) -> str:
                    if args:
                        return prefix + str(args[0]) + suffix
                    if kwargs:
                        if len(kwargs) == 1:
                            return prefix + str(next(iter(kwargs.values()))) + suffix
                        return template
                    return template

            else:

                def key_fn(*args, **kwargs) -> str:
                    if args:
                        try:
                            return template.format(args[0])
                        except Exception:
                            return template
                    if kwargs:
                        try:
                            return template.format(**kwargs)
                        except Exception:
                            if len(kwargs) == 1:
                                try:
                                    return template.format(next(iter(kwargs.values())))
                                except Exception:
                                    return template
                            return template
                    return template

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            def wrapper(*args, **kwargs) -> T:
                # If ttl is 0 or negative, disable caching and SWR behavior
                if ttl <= 0:
                    return func(*args, **kwargs)
                cache_key = key_fn(*args, **kwargs)

                cache_obj = get_cache()
                now = time.time()

                # Try to get from cache
                entry = cache_obj.get_entry(cache_key)

                if entry is None:
                    # Cache miss - fetch now
                    result = func(*args, **kwargs)
                    cache_entry = CacheEntry(
                        value=result, fresh_until=now + ttl, created_at=now
                    )
                    cache_obj.set_entry(cache_key, cache_entry)
                    return result

                if now < entry.fresh_until:
                    return entry.value

                age = now - entry.created_at
                if age > (ttl + stale_ttl):
                    # Too stale, fetch now
                    result = func(*args, **kwargs)
                    cache_entry = CacheEntry(
                        value=result, fresh_until=now + ttl, created_at=now
                    )
                    cache_obj.set_entry(cache_key, cache_entry)
                    return result

                # Stale but within grace period - return stale and refresh in background
                # Try to acquire refresh lock
                lock_key = f"{cache_key}:refresh_lock"
                if enable_lock:
                    acquired = cache_obj.set_if_not_exists(
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
                        cache_obj.set_entry(cache_key, cache_entry)
                    except Exception:
                        # Log background refresh failures but never raise
                        logger.exception(
                            "SWR background refresh failed for key %r", cache_key
                        )

                # Use a shared executor to avoid per-refresh thread creation overhead.
                _get_swr_executor().submit(refresh_job)

                return entry.value

            wrapper.__wrapped__ = func  # type: ignore
            wrapper.__name__ = func.__name__  # type: ignore
            wrapper.__doc__ = func.__doc__  # type: ignore
            wrapper._cache = get_cache()  # type: ignore
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

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        """Stop the shared background scheduler."""
        with cls._lock:
            if cls._started and cls._scheduler is not None:
                cls._scheduler.shutdown(wait=wait)
                cls._started = False
                cls._scheduler = None


# ============================================================================
# BGCache - Background cache loader decorator
# ============================================================================


class BackgroundCache:
    """
    Background cache with BackgroundScheduler for periodic data loading.
    All instances share ONE BackgroundScheduler, but each has its own cache storage.
    Works with both sync and async functions.

    Args (public API, unified naming):
        key (str): Unique cache key for the loader.
        interval_seconds (int): Refresh interval.
        ttl (int | None): TTL for cached value (defaults to 2 * interval_seconds).

    Example:
        # Async function
        @BGCache.register_loader(key="categories", interval_seconds=300)
        async def load_categories():
            return await db.query("SELECT * FROM categories")

        # Sync function
        @BGCache.register_loader(key="config", interval_seconds=300)
        def load_config():
            return {"key": "value"}

        # With custom cache backend
        @BGCache.register_loader(key="products", interval_seconds=300, cache=redis_cache)
        def load_products():
            return fetch_products_from_db()
    """

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        """Stop the shared BackgroundScheduler."""
        _SharedScheduler.shutdown(wait)

    @classmethod
    def register_loader(
        cls,
        key: str,
        interval_seconds: int,
        ttl: int | None = None,
        run_immediately: bool = True,
        on_error: Callable[[Exception], None] | None = None,
        cache: CacheStorage | Callable[[], CacheStorage] | None = None,
    ) -> Callable[[Callable[[], T]], Callable[[], T]]:
        """Register a background data loader.

        Args:
            key: Unique cache key to store the loaded data.
            interval_seconds: How often to refresh the data (in seconds).
            ttl: Cache TTL (defaults to 2 * interval_seconds if None).
            run_immediately: Whether to load data immediately on registration.
            on_error: Optional error handler callback.
            cache: Optional cache backend (InMemCache, RedisCache, etc.).

        Returns:
            Decorated function that returns cached data (sync or async).
        """
        cache_key = key
        # If interval_seconds <= 0 or ttl == 0, disable background scheduling and caching.
        if interval_seconds <= 0:
            interval_seconds = 0
        if ttl is None and interval_seconds > 0:
            ttl = interval_seconds * 2
        if ttl is None:
            ttl = 0

        # Create a dedicated cache instance for this loader
        cache_factory: Callable[[], CacheStorage]
        if cache is None:
            cache_factory = InMemCache
        elif callable(cache):
            cache_factory = cache  # type: ignore[assignment]
        else:
            cache_instance = cache

            def cache_factory() -> CacheStorage:
                return cache_instance

        loader_cache: CacheStorage | None = None
        cache_init_lock = threading.Lock()

        def get_cache() -> CacheStorage:
            nonlocal loader_cache
            if loader_cache is None:
                with cache_init_lock:
                    if loader_cache is None:
                        loader_cache = cache_factory()
            return loader_cache

        def decorator(loader_func: Callable[[], T]) -> Callable[[], T]:
            # Detect if function is async
            is_async = asyncio.iscoroutinefunction(loader_func)
            # Single-flight lock to avoid duplicate initial loads under concurrency
            loader_lock = asyncio.Lock() if is_async else threading.Lock()

            # If no scheduling/caching is desired, just wrap the function and call through
            if interval_seconds <= 0 or ttl <= 0:
                if is_async:

                    async def async_wrapper() -> T:
                        return await loader_func()

                    async_wrapper.__wrapped__ = loader_func  # type: ignore
                    async_wrapper.__name__ = loader_func.__name__  # type: ignore
                    async_wrapper.__doc__ = loader_func.__doc__  # type: ignore
                    async_wrapper._cache = loader_cache  # type: ignore
                    async_wrapper._cache_key = cache_key  # type: ignore

                    return async_wrapper  # type: ignore
                else:

                    def sync_wrapper() -> T:
                        return loader_func()

                    sync_wrapper.__wrapped__ = loader_func  # type: ignore
                    sync_wrapper.__name__ = loader_func.__name__  # type: ignore
                    sync_wrapper.__doc__ = loader_func.__doc__  # type: ignore
                    sync_wrapper._cache = loader_cache  # type: ignore
                    sync_wrapper._cache_key = cache_key  # type: ignore

                    return sync_wrapper  # type: ignore

            # Create wrapper that loads and caches
            def refresh_job():
                """Job that runs periodically to refresh the cache."""
                try:
                    cache_obj = get_cache()
                    if is_async:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            data = loop.run_until_complete(loader_func())
                        finally:
                            loop.close()
                    else:
                        data = loader_func()

                    cache_obj.set(cache_key, data, ttl)
                except Exception as e:
                    # User-provided error handler gets first chance
                    if on_error:
                        try:
                            on_error(e)
                        except Exception:
                            # Avoid user handler breaking the scheduler
                            logger.exception(
                                "BGCache error handler failed for key %r", cache_key
                            )
                    else:
                        # Log uncaught loader errors for visibility
                        logger.exception(
                            "BGCache refresh job failed for key %r", cache_key
                        )

            # Get shared scheduler
            scheduler = _SharedScheduler.get_scheduler()

            # Run immediately if requested (but only if cache is empty)
            if run_immediately:
                cache_obj = get_cache()
                if cache_obj.get(cache_key) is None:
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
                    cache_obj = get_cache()
                    value = cache_obj.get(cache_key)
                    if value is not None:
                        return value
                    async with loader_lock:  # type: ignore[arg-type]
                        value = cache_obj.get(cache_key)
                        if value is not None:
                            return value
                        result = await loader_func()
                        cache_obj.set(cache_key, result, ttl)
                        return result

                async_wrapper.__wrapped__ = loader_func  # type: ignore
                async_wrapper.__name__ = loader_func.__name__  # type: ignore
                async_wrapper.__doc__ = loader_func.__doc__  # type: ignore
                async_wrapper._cache = get_cache()  # type: ignore
                async_wrapper._cache_key = cache_key  # type: ignore

                return async_wrapper  # type: ignore
            else:

                def sync_wrapper() -> T:
                    """Get cached data or call loader if not available."""
                    cache_obj = get_cache()
                    value = cache_obj.get(cache_key)
                    if value is not None:
                        return value
                    with loader_lock:  # type: ignore[arg-type]
                        value = cache_obj.get(cache_key)
                        if value is not None:
                            return value
                        result = loader_func()
                        cache_obj.set(cache_key, result, ttl)
                        return result

                sync_wrapper.__wrapped__ = loader_func  # type: ignore
                sync_wrapper.__name__ = loader_func.__name__  # type: ignore
                sync_wrapper.__doc__ = loader_func.__doc__  # type: ignore
                sync_wrapper._cache = get_cache()  # type: ignore
                sync_wrapper._cache_key = cache_key  # type: ignore

                return sync_wrapper  # type: ignore

        return decorator


# Alias for shorter usage
BGCache = BackgroundCache
