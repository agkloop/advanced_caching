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
import time
from datetime import datetime, timedelta
from typing import Callable, TypeVar

from apscheduler.triggers.interval import IntervalTrigger

from ._decorator_common import attach_wrapper_metadata, normalize_cache_factory
from ._schedulers import SharedAsyncScheduler, SharedScheduler
from .storage import CacheEntry, CacheStorage, InMemCache

T = TypeVar("T")

# Minimal logger used only for error reporting (no debug/info on hot paths)
logger = logging.getLogger(__name__)


# Helper to normalize cache key builders for all decorators.
def _create_key_fn(key: str | Callable[..., str]) -> Callable[..., str]:
    if callable(key):
        return key  # type: ignore[assignment]

    template = key
    if "{" not in template:

        def key_fn(*args, **kwargs) -> str:
            return template

        return key_fn

    if (
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

        return key_fn

    def key_fn(*args, **kwargs) -> str:
        if args:
            try:
                return template.format(args[0])
            except Exception:
                try:
                    return template.format(*args)
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

    return key_fn


# ============================================================================
# TTLCache - Simple TTL-based caching decorator
# ============================================================================


class AsyncTTLCache:
    """
    Simple TTL cache decorator (singleton pattern).
    Each decorated function gets its own cache instance.
    Supports both sync and async functions (preserves sync/async nature).

    Key templates (high-performance, simple):
    - Positional placeholder: "user:{}" → first positional arg
    - Named placeholder: "user:{user_id}" → keyword arg `user_id`
    - Custom function: key=lambda *a, **k: ...

    Examples:
        @TTLCache.cached("user:{}", ttl=60)
        async def get_user(user_id):
            return await db.fetch_user(user_id)
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
        """
        key_fn = _create_key_fn(key)
        cache_factory = normalize_cache_factory(cache, default_factory=InMemCache)

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            cache_obj = cache_factory()
            cache_get_entry = cache_obj.get_entry
            cache_set = cache_obj.set
            now_fn = time.time

            if asyncio.iscoroutinefunction(func):

                async def async_wrapper(*args, **kwargs) -> T:
                    if ttl <= 0:
                        return await func(*args, **kwargs)

                    cache_key = key_fn(*args, **kwargs)
                    entry = cache_get_entry(cache_key)
                    if entry is not None:
                        if now_fn() < entry.fresh_until:
                            return entry.value

                    result = await func(*args, **kwargs)
                    cache_set(cache_key, result, ttl)
                    return result

                attach_wrapper_metadata(async_wrapper, func, cache_obj=cache_obj)
                return async_wrapper  # type: ignore

            # Sync wrapper
            def sync_wrapper(*args, **kwargs) -> T:
                if ttl <= 0:
                    return func(*args, **kwargs)

                cache_key = key_fn(*args, **kwargs)
                entry = cache_get_entry(cache_key)
                if entry is not None:
                    if now_fn() < entry.fresh_until:
                        return entry.value

                result = func(*args, **kwargs)
                cache_set(cache_key, result, ttl)
                return result

            attach_wrapper_metadata(sync_wrapper, func, cache_obj=cache_obj)
            return sync_wrapper

        return decorator


# Alias for easier import
TTLCache = AsyncTTLCache


# ============================================================================
# SWRCache - Stale-While-Revalidate pattern
# ============================================================================


class AsyncStaleWhileRevalidateCache:
    """
    SWR (Stale-While-Revalidate) cache decorator.
    Supports both sync and async functions.
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
        key_fn = _create_key_fn(key)
        cache_factory = normalize_cache_factory(cache, default_factory=InMemCache)

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            cache_obj = cache_factory()
            get_entry = cache_obj.get_entry
            set_entry = cache_obj.set_entry
            set_if_not_exists = cache_obj.set_if_not_exists
            now_fn = time.time

            if asyncio.iscoroutinefunction(func):
                create_task = asyncio.create_task

                async def async_wrapper(*args, **kwargs) -> T:
                    if ttl <= 0:
                        return await func(*args, **kwargs)
                    cache_key = key_fn(*args, **kwargs)
                    now = now_fn()
                    entry = get_entry(cache_key)

                    if entry is None:
                        result = await func(*args, **kwargs)
                        created_at = now_fn()
                        set_entry(
                            cache_key,
                            CacheEntry(
                                value=result,
                                fresh_until=created_at + ttl,
                                created_at=created_at,
                            ),
                        )
                        return result

                    if now < entry.fresh_until:
                        return entry.value

                    if (now - entry.created_at) > (ttl + stale_ttl):
                        result = await func(*args, **kwargs)
                        created_at = now_fn()
                        set_entry(
                            cache_key,
                            CacheEntry(
                                value=result,
                                fresh_until=created_at + ttl,
                                created_at=created_at,
                            ),
                        )
                        return result

                    if enable_lock:
                        lock_key = f"{cache_key}:refresh_lock"
                        if not set_if_not_exists(lock_key, "1", stale_ttl or 10):
                            return entry.value

                    async def refresh_job() -> None:
                        try:
                            new_value = await func(*args, **kwargs)
                            refreshed_at = now_fn()
                            set_entry(
                                cache_key,
                                CacheEntry(
                                    value=new_value,
                                    fresh_until=refreshed_at + ttl,
                                    created_at=refreshed_at,
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "Async SWR background refresh failed for key %r",
                                cache_key,
                            )

                    create_task(refresh_job())
                    return entry.value

                attach_wrapper_metadata(async_wrapper, func, cache_obj=cache_obj)
                return async_wrapper  # type: ignore

            # Sync wrapper
            def sync_wrapper(*args, **kwargs) -> T:
                if ttl <= 0:
                    return func(*args, **kwargs)
                cache_key = key_fn(*args, **kwargs)
                now = now_fn()
                entry = get_entry(cache_key)

                if entry is None:
                    result = func(*args, **kwargs)
                    created_at = now_fn()
                    set_entry(
                        cache_key,
                        CacheEntry(
                            value=result,
                            fresh_until=created_at + ttl,
                            created_at=created_at,
                        ),
                    )
                    return result

                if now < entry.fresh_until:
                    return entry.value

                if (now - entry.created_at) > (ttl + stale_ttl):
                    result = func(*args, **kwargs)
                    created_at = now_fn()
                    set_entry(
                        cache_key,
                        CacheEntry(
                            value=result,
                            fresh_until=created_at + ttl,
                            created_at=created_at,
                        ),
                    )
                    return result

                if enable_lock:
                    lock_key = f"{cache_key}:refresh_lock"
                    if not set_if_not_exists(lock_key, "1", stale_ttl or 10):
                        return entry.value

                def refresh_job() -> None:
                    try:
                        new_value = func(*args, **kwargs)
                        refreshed_at = now_fn()
                        set_entry(
                            cache_key,
                            CacheEntry(
                                value=new_value,
                                fresh_until=refreshed_at + ttl,
                                created_at=refreshed_at,
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "Sync SWR background refresh failed for key %r", cache_key
                        )

                # Run refresh in background using SharedScheduler
                scheduler = SharedScheduler.get_scheduler()
                SharedScheduler.start()
                scheduler.add_job(refresh_job)
                return entry.value

            attach_wrapper_metadata(sync_wrapper, func, cache_obj=cache_obj)
            return sync_wrapper

        return decorator


SWRCache = AsyncStaleWhileRevalidateCache


# Schedulers are implemented as internal singletons in `advanced_caching._schedulers`.


# ============================================================================
# BGCache - Background cache loader decorator
# ============================================================================


class AsyncBackgroundCache:
    """Background cache loader that uses APScheduler (AsyncIOScheduler for async, BackgroundScheduler for sync)."""

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        SharedAsyncScheduler.shutdown(wait)
        SharedScheduler.shutdown(wait)

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
        cache_key = key
        if interval_seconds <= 0:
            interval_seconds = 0
        if ttl is None and interval_seconds > 0:
            ttl = interval_seconds * 2
        if ttl is None:
            ttl = 0

        cache_factory = normalize_cache_factory(cache, default_factory=InMemCache)
        cache_obj = cache_factory()
        cache_get = cache_obj.get
        cache_set = cache_obj.set

        def decorator(loader_func: Callable[[], T]) -> Callable[[], T]:
            if asyncio.iscoroutinefunction(loader_func):
                loader_lock: asyncio.Lock | None = None
                initial_load_done = False
                initial_load_task: asyncio.Task[None] | None = None

                if interval_seconds <= 0 or ttl <= 0:

                    async def async_wrapper() -> T:
                        return await loader_func()

                    attach_wrapper_metadata(
                        async_wrapper,
                        loader_func,
                        cache_obj=cache_obj,
                        cache_key=cache_key,
                    )
                    return async_wrapper  # type: ignore

                async def refresh_job() -> None:
                    try:
                        data = await loader_func()
                        cache_set(cache_key, data, ttl)
                    except Exception as e:
                        if on_error:
                            try:
                                on_error(e)
                            except Exception:
                                logger.exception(
                                    "Async BGCache error handler failed for key %r",
                                    cache_key,
                                )
                        else:
                            logger.exception(
                                "Async BGCache refresh job failed for key %r", cache_key
                            )

                next_run_time: datetime | None = None

                if run_immediately:
                    if cache_get(cache_key) is None:
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            asyncio.run(refresh_job())
                            initial_load_done = True
                            next_run_time = datetime.now() + timedelta(
                                seconds=interval_seconds * 2
                            )
                        else:
                            initial_load_task = loop.create_task(refresh_job())
                            next_run_time = datetime.now() + timedelta(
                                seconds=interval_seconds * 2
                            )

                scheduler = SharedAsyncScheduler.get_scheduler()
                SharedAsyncScheduler.ensure_started()
                scheduler.add_job(
                    refresh_job,
                    trigger=IntervalTrigger(seconds=interval_seconds),
                    id=cache_key,
                    replace_existing=True,
                    next_run_time=next_run_time,
                )

                async def async_wrapper() -> T:
                    nonlocal loader_lock, initial_load_done, initial_load_task

                    value = cache_get(cache_key)
                    if value is not None:
                        return value

                    # Miss path: serialize initial load / fallback loads.
                    # We create the asyncio.Lock lazily to avoid requiring a running
                    # loop at decoration/import time.
                    if loader_lock is None:
                        loader_lock = asyncio.Lock()
                    async with loader_lock:
                        value = cache_get(cache_key)
                        if value is not None:
                            return value

                        # If we scheduled an initial refresh task, wait for it once.
                        if not initial_load_done:
                            if initial_load_task is not None:
                                await initial_load_task
                            elif not run_immediately:
                                await refresh_job()
                            initial_load_done = True

                        value = cache_get(cache_key)
                        if value is not None:
                            return value
                        result = await loader_func()
                        cache_set(cache_key, result, ttl)
                        return result

                attach_wrapper_metadata(
                    async_wrapper, loader_func, cache_obj=cache_obj, cache_key=cache_key
                )
                return async_wrapper  # type: ignore

            # Sync wrapper
            from threading import Lock

            sync_lock = Lock()
            sync_initial_load_done = False

            if interval_seconds <= 0 or ttl <= 0:

                def sync_wrapper() -> T:
                    return loader_func()

                attach_wrapper_metadata(
                    sync_wrapper, loader_func, cache_obj=cache_obj, cache_key=cache_key
                )
                return sync_wrapper

            def sync_refresh_job() -> None:
                try:
                    data = loader_func()
                    cache_set(cache_key, data, ttl)
                except Exception as e:
                    if on_error:
                        try:
                            on_error(e)
                        except Exception:
                            logger.exception(
                                "Sync BGCache error handler failed for key %r",
                                cache_key,
                            )
                    else:
                        logger.exception(
                            "Sync BGCache refresh job failed for key %r", cache_key
                        )

            next_run_time_sync: datetime | None = None

            if run_immediately:
                if cache_get(cache_key) is None:
                    sync_refresh_job()
                    sync_initial_load_done = True
                    next_run_time_sync = datetime.now() + timedelta(
                        seconds=interval_seconds * 2
                    )

            scheduler_sync = SharedScheduler.get_scheduler()
            SharedScheduler.start()
            scheduler_sync.add_job(
                sync_refresh_job,
                trigger=IntervalTrigger(seconds=interval_seconds),
                id=cache_key,
                replace_existing=True,
                next_run_time=next_run_time_sync,
            )

            def sync_wrapper_fn() -> T:
                nonlocal sync_initial_load_done
                value = cache_get(cache_key)
                if value is not None:
                    return value

                with sync_lock:
                    value = cache_get(cache_key)
                    if value is not None:
                        return value

                    if not sync_initial_load_done:
                        if not run_immediately:
                            sync_refresh_job()
                        sync_initial_load_done = True

                    value = cache_get(cache_key)
                    if value is not None:
                        return value
                    result = loader_func()
                    cache_set(cache_key, result, ttl)
                    return result

            attach_wrapper_metadata(
                sync_wrapper_fn, loader_func, cache_obj=cache_obj, cache_key=cache_key
            )
            return sync_wrapper_fn

        return decorator


BGCache = AsyncBackgroundCache
