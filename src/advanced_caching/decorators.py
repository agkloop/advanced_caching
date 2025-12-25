"""
Cache decorators for function result caching.

Provides:
- TTLCache: Simple TTL-based caching
- SWRCache: Stale-while-revalidate pattern
- BGCache: Background scheduler-based loading with APScheduler
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timedelta
from typing import Callable, TypeVar, Any
from dataclasses import dataclass

from apscheduler.triggers.interval import IntervalTrigger

from ._decorator_common import attach_wrapper_metadata, normalize_cache_factory
from ._schedulers import SharedAsyncScheduler, SharedScheduler
from .storage import CacheEntry, CacheStorage, InMemCache

T = TypeVar("T")

# Minimal logger used only for error reporting (no debug/info on hot paths)
logger = logging.getLogger(__name__)


# Helper to normalize cache key builders for all decorators.
def _create_smart_key_fn(
    key: str | Callable[..., str], func: Callable[..., Any]
) -> Callable[..., str]:
    # If the key is already a function (e.g., lambda u: f"user:{u}"), return it directly.
    if callable(key):
        return key  # type: ignore[assignment]

    template = key
    # Optimization: Static key (e.g., "global_config")
    # If there are no placeholders, we don't need to format anything.
    if "{" not in template:

        def key_fn(*args, **kwargs) -> str:
            # Always return the static string, ignoring arguments.
            return template

        return key_fn

    # Optimization: Simple positional key "prefix:{}" (e.g., "user:{}")
    # This is a very common pattern, so we optimize it to avoid full string formatting.
    if template.count("{}") == 1 and template.count("{") == 1:
        prefix, suffix = template.split("{}", 1)

        def key_fn(*args, **kwargs) -> str:
            # If positional args are provided (e.g., get_user(123)), use the first one.
            if args:
                return f"{prefix}{args[0]}{suffix}"
            # If keyword args are provided (e.g., get_user(user_id=123)), use the first value.
            # This supports the case where a positional placeholder is used but the function is called with kwargs.
            if kwargs:
                # Fallback for single kwarg usage with positional template
                return f"{prefix}{next(iter(kwargs.values()))}{suffix}"
            # If no arguments are provided, return the raw template (e.g., "user:{}").
            return template

        return key_fn

    # General case: Named placeholders (e.g., "user:{id}") or complex positional (e.g., "{}:{}" or "{0}")
    # We need to inspect the function signature to map positional arguments to parameter names.
    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())

    # Pre-compute defaults to handle cases where arguments are omitted but have default values.
    # e.g., def func(a=1): ... with key="{a}"
    defaults = {
        k: v.default
        for k, v in sig.parameters.items()
        if v.default is not inspect.Parameter.empty
    }

    def key_fn(*args, **kwargs) -> str:
        # Fast merge of arguments to support named placeholders.
        # 1. Start with defaults (e.g., {'a': 1})
        merged = defaults.copy() if defaults else {}

        # 2. Map positional args to names (e.g., func(2) -> {'a': 2})
        # This allows us to use named placeholders even when the function is called positionally.
        if args:
            merged.update(zip(param_names, args))

        # 3. Update with explicit kwargs (e.g., func(a=3) -> {'a': 3})
        if kwargs:
            merged.update(kwargs)

        try:
            # Try formatting with named arguments (e.g., "user:{id}".format(id=123))
            return template.format(**merged)
        except (KeyError, ValueError, IndexError):
            # Fallback: Try raw positional args (for "{}" templates or mixed usage)
            # e.g., "user:{}".format(123) if named formatting failed.
            try:
                return template.format(*args)
            except Exception:
                # If formatting fails entirely, return the raw template to avoid crashing.
                return template
        except Exception:
            # Catch-all for other formatting errors.
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
    def configure(
        cls, cache: CacheStorage | Callable[[], CacheStorage]
    ) -> type[AsyncTTLCache]:
        """
        Create a configured version of TTLCache with a default cache backend.

        Example:
            MyCache = TTLCache.configure(cache=RedisCache(...))
            @MyCache.cached("key", ttl=60)
            def func(): ...
        """

        class ConfiguredTTLCache(cls):
            @classmethod
            def cached(
                cls_inner,
                key: str | Callable[..., str],
                ttl: int,
                cache: CacheStorage | Callable[[], CacheStorage] | None = None,
            ) -> Callable[[Callable[..., T]], Callable[..., T]]:
                # Use the configured cache if none is provided
                return cls.cached(key, ttl, cache=cache or cls_inner._configured_cache)

        ConfiguredTTLCache._configured_cache = cache  # type: ignore
        return ConfiguredTTLCache

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
        cache_factory = normalize_cache_factory(cache, default_factory=InMemCache)

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            key_fn = _create_smart_key_fn(key, func)
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
    def configure(
        cls, cache: CacheStorage | Callable[[], CacheStorage]
    ) -> type[AsyncStaleWhileRevalidateCache]:
        """
        Create a configured version of SWRCache with a default cache backend.
        """

        class ConfiguredSWRCache(cls):
            @classmethod
            def cached(
                cls_inner,
                key: str | Callable[..., str],
                ttl: int,
                stale_ttl: int = 0,
                cache: CacheStorage | Callable[[], CacheStorage] | None = None,
                enable_lock: bool = True,
            ) -> Callable[[Callable[..., T]], Callable[..., T]]:
                return cls.cached(
                    key,
                    ttl,
                    stale_ttl=stale_ttl,
                    cache=cache or cls_inner._configured_cache,
                    enable_lock=enable_lock,
                )

        ConfiguredSWRCache._configured_cache = cache  # type: ignore
        return ConfiguredSWRCache

    @classmethod
    def cached(
        cls,
        key: str | Callable[..., str],
        ttl: int,
        stale_ttl: int = 0,
        cache: CacheStorage | Callable[[], CacheStorage] | None = None,
        enable_lock: bool = True,
    ) -> Callable[[Callable[..., T]], Callable[..., T]]:
        cache_factory = normalize_cache_factory(cache, default_factory=InMemCache)

        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            key_fn = _create_smart_key_fn(key, func)
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

    # Global registry to enforce single writer per cache key across all configured BGCache classes.
    _writer_registry: dict[str, "_WriterRecord"] = {}

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        SharedAsyncScheduler.shutdown(wait)
        SharedScheduler.shutdown(wait)
        cls._writer_registry.clear()

    @classmethod
    def configure(
        cls, cache: CacheStorage | Callable[[], CacheStorage]
    ) -> type[AsyncBackgroundCache]:
        """
        Create a configured version of BGCache with a default cache backend.
        """

        class ConfiguredBGCache(cls):
            @classmethod
            def register_loader(
                cls_inner,
                key: str,
                interval_seconds: int,
                ttl: int | None = None,
                run_immediately: bool = True,
                on_error: Callable[[Exception], None] | None = None,
                cache: CacheStorage | Callable[[], CacheStorage] | None = None,
            ) -> Callable[[Callable[[], T]], Callable[[], T]]:
                return cls.register_loader(
                    key,
                    interval_seconds,
                    ttl=ttl,
                    run_immediately=run_immediately,
                    on_error=on_error,
                    cache=cache or cls_inner._configured_cache,
                )

        ConfiguredBGCache._configured_cache = cache  # type: ignore
        return ConfiguredBGCache

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

    @classmethod
    def register_writer(
        cls,
        key: str,
        interval_seconds: int,
        ttl: int | None = None,
        run_immediately: bool = True,
        on_error: Callable[[Exception], None] | None = None,
        cache: CacheStorage | Callable[[], CacheStorage] | None = None,
    ) -> Callable[[Callable[[], T]], Callable[[], T]]:
        cache_key = key
        if cache_key in cls._writer_registry:
            raise ValueError(f"BGCache writer already registered for key '{cache_key}'")

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

                async def run_once() -> T:
                    nonlocal loader_lock
                    if loader_lock is None:
                        loader_lock = asyncio.Lock()
                    async with loader_lock:
                        try:
                            data = await loader_func()
                            cache_set(cache_key, data, ttl)
                            return data
                        except Exception as e:  # pragma: no cover - defensive
                            if on_error:
                                try:
                                    on_error(e)
                                except Exception:
                                    logger.exception(
                                        "Async BGCache error handler failed for key %r",
                                        cache_key,
                                    )
                            logger.exception(
                                "Async BGCache writer failed for key %r", cache_key
                            )
                            raise

                async def refresh_job() -> None:
                    try:
                        await run_once()
                    except Exception:
                        # Error already handled/logged inside run_once
                        pass

                next_run_time: datetime | None = None

                if run_immediately:
                    if cache_get(cache_key) is None:
                        try:
                            loop = asyncio.get_running_loop()
                        except RuntimeError:
                            asyncio.run(refresh_job())
                            next_run_time = datetime.now() + timedelta(
                                seconds=interval_seconds * 2
                            )
                        else:
                            loop.create_task(refresh_job())
                            next_run_time = datetime.now() + timedelta(
                                seconds=interval_seconds * 2
                            )

                if interval_seconds > 0:
                    scheduler = SharedAsyncScheduler.get_scheduler()
                    SharedAsyncScheduler.ensure_started()
                    scheduler.add_job(
                        refresh_job,
                        trigger=IntervalTrigger(seconds=interval_seconds),
                        id=cache_key,
                        replace_existing=True,
                        next_run_time=next_run_time,
                    )

                async def writer_wrapper() -> T:
                    value = cache_get(cache_key)
                    if value is not None:
                        return value  # type: ignore[return-value]
                    return await run_once()

                attach_wrapper_metadata(
                    writer_wrapper,
                    loader_func,
                    cache_obj=cache_obj,
                    cache_key=cache_key,
                )
                cls._writer_registry[cache_key] = _WriterRecord(
                    cache_key=cache_key,
                    cache=cache_obj,
                    ttl=ttl,
                    loader_wrapper=writer_wrapper,
                    is_async=True,
                )
                return writer_wrapper  # type: ignore

            # Sync writer path
            from threading import Lock

            loader_lock = Lock()

            def run_once_sync() -> T:
                with loader_lock:
                    try:
                        data = loader_func()
                        cache_set(cache_key, data, ttl)
                        return data
                    except Exception as e:  # pragma: no cover - defensive
                        if on_error:
                            try:
                                on_error(e)
                            except Exception:
                                logger.exception(
                                    "Sync BGCache error handler failed for key %r",
                                    cache_key,
                                )
                        logger.exception(
                            "Sync BGCache writer failed for key %r", cache_key
                        )
                        raise

            def refresh_job_sync() -> None:
                try:
                    run_once_sync()
                except Exception:
                    # Error already handled/logged inside run_once_sync
                    pass

            next_run_time_sync: datetime | None = None

            if run_immediately:
                if cache_get(cache_key) is None:
                    refresh_job_sync()
                    next_run_time_sync = datetime.now() + timedelta(
                        seconds=interval_seconds * 2
                    )

            if interval_seconds > 0:
                scheduler_sync = SharedScheduler.get_scheduler()
                SharedScheduler.start()
                scheduler_sync.add_job(
                    refresh_job_sync,
                    trigger=IntervalTrigger(seconds=interval_seconds),
                    id=cache_key,
                    replace_existing=True,
                    next_run_time=next_run_time_sync,
                )

            def writer_wrapper_sync() -> T:
                value = cache_get(cache_key)
                if value is not None:
                    return value  # type: ignore[return-value]
                return run_once_sync()

            attach_wrapper_metadata(
                writer_wrapper_sync,
                loader_func,
                cache_obj=cache_obj,
                cache_key=cache_key,
            )
            cls._writer_registry[cache_key] = _WriterRecord(
                cache_key=cache_key,
                cache=cache_obj,
                ttl=ttl,
                loader_wrapper=writer_wrapper_sync,
                is_async=False,
            )
            return writer_wrapper_sync  # type: ignore

        return decorator

    @classmethod
    def get_reader(
        cls,
        key: str,
        interval_seconds: int,
        ttl: int | None = None,
        *,
        run_immediately: bool = True,
        on_error: Callable[[Exception], None] | None = None,
        cache: CacheStorage | Callable[[], CacheStorage] | None = None,
    ) -> Callable[[], T | None]:
        cache_key = key

        if interval_seconds <= 0:
            interval_seconds = 0
        if ttl is None and interval_seconds > 0:
            ttl = interval_seconds * 2
        if ttl is None:
            ttl = 0

        # Source cache (shared/distributed) to pull from; local_cache used for fast reads.
        source_cache_factory = normalize_cache_factory(
            cache, default_factory=InMemCache
        )
        source_cache = source_cache_factory()
        local_cache = InMemCache()
        source_get = source_cache.get
        local_get = local_cache.get
        local_set = local_cache.set

        def load_once() -> None:
            try:
                value = source_get(cache_key)
                if value is not None:
                    local_set(cache_key, value, ttl)
            except Exception as e:  # pragma: no cover - defensive
                if on_error:
                    try:
                        on_error(e)
                    except Exception:
                        logger.exception(
                            "BGCache reader on_error failed for key %r", cache_key
                        )
                else:
                    logger.exception(
                        "BGCache reader refresh failed for key %r", cache_key
                    )

        if run_immediately and (interval_seconds > 0 or ttl > 0):
            load_once()

        if interval_seconds > 0:
            scheduler_sync = SharedScheduler.get_scheduler()
            SharedScheduler.start()
            scheduler_sync.add_job(
                load_once,
                trigger=IntervalTrigger(seconds=interval_seconds),
                id=f"reader:{cache_key}",
                replace_existing=True,
            )

        def read_only_reader() -> T | None:
            value = local_get(cache_key)
            if value is not None:
                return value
            # Fallback: pull once from source on demand if not already cached.
            load_once()
            return local_get(cache_key)

        attach_wrapper_metadata(
            read_only_reader,
            read_only_reader,
            cache_obj=local_cache,
            cache_key=cache_key,
        )
        return read_only_reader  # type: ignore


BGCache = AsyncBackgroundCache


@dataclass(slots=True)
class _WriterRecord:
    cache_key: str
    cache: CacheStorage
    ttl: int
    loader_wrapper: Callable[[], Any] | Callable[[], Any]
    is_async: bool
