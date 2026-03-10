"""Two public symbols: ``cache`` and ``bg``.

Quick start::

    from advanced_caching import cache, bg

    # TTL cache — same for sync and async
    @cache(60, key="user:{user_id}")
    async def get_user(user_id: int) -> User:
        return await db.fetch(user_id)

    # Stale-while-revalidate (serve stale for 30 s, refresh in background)
    @cache(60, stale=30, key="feed:{}")
    async def get_feed(user_id): ...

    # Background loader — auto-refreshed every 5 min
    @bg(300, key="app_config")
    async def load_config():
        return await fetch_remote_config()

    config = await load_config()  # served from cache

    # Invalidation
    get_user.invalidate(user_id=42)   # delete one entry
    get_user.clear()                  # wipe all entries
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from datetime import datetime, timedelta
from threading import Lock as _ThreadLock
from typing import Any, Callable, ClassVar, TypeVar

from apscheduler.triggers.interval import IntervalTrigger

from ._schedulers import SharedAsyncScheduler, SharedScheduler
from .metrics import MetricsCollector
from .storage import CacheEntry, CacheStorage, InMemCache, InstrumentedStorage

F = TypeVar("F", bound=Callable[..., Any])
T = TypeVar("T")

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# Key generation
# ──────────────────────────────────────────────────────────────────────────────


def _make_key_fn(
    key: str | Callable[..., str],
    func: Callable[..., Any],
) -> Callable[..., str]:
    """Build a fast cache-key function from a template string or callable."""
    if callable(key):
        return key  # type: ignore[return-value]

    template: str = key

    # Static key — no placeholders
    if "{" not in template:
        return lambda *a, **kw: template

    # Single positional placeholder "prefix:{}" — very common, optimise
    if template.count("{}") == 1 and template.count("{") == 1:
        prefix, suffix = template.split("{}", 1)

        def _pos(*args: Any, **kwargs: Any) -> str:
            if args:
                return f"{prefix}{args[0]}{suffix}"
            if kwargs:
                return f"{prefix}{next(iter(kwargs.values()))}{suffix}"
            return template

        return _pos

    # Named / complex placeholders — inspect once at decoration time
    sig = inspect.signature(func)
    param_names = list(sig.parameters.keys())
    defaults = {
        k: v.default
        for k, v in sig.parameters.items()
        if v.default is not inspect.Parameter.empty
    }

    def _named(*args: Any, **kwargs: Any) -> str:
        merged: dict[str, Any] = defaults.copy() if defaults else {}
        if args:
            merged.update(zip(param_names, args))
        if kwargs:
            merged.update(kwargs)
        try:
            return template.format(**merged)
        except (KeyError, ValueError, IndexError):
            try:
                return template.format(*args)
            except Exception:
                return template
        except Exception:
            return template

    return _named


# ──────────────────────────────────────────────────────────────────────────────
# Store normalisation
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_store(
    store: CacheStorage | Callable[[], CacheStorage] | None,
) -> CacheStorage:
    """Accept a store instance, a factory callable, or None (→ InMemCache)."""
    if store is None:
        return InMemCache()
    if callable(store) and not hasattr(store, "get"):
        return store()  # it's a factory / class
    return store  # type: ignore[return-value]


# ──────────────────────────────────────────────────────────────────────────────
# Wrapper metadata
# ──────────────────────────────────────────────────────────────────────────────


def _attach(
    wrapper: Any,
    func: Callable[..., Any],
    *,
    store: CacheStorage,
    key_fn: Callable[..., str] | None = None,
    static_key: str | None = None,
) -> None:
    """Attach ``store``, ``invalidate``, ``clear``, and ``__wrapped__`` to wrapper."""
    wrapper.__wrapped__ = func
    wrapper.__name__ = getattr(func, "__name__", "wrapper")
    wrapper.__doc__ = func.__doc__
    wrapper.store = store

    if key_fn is not None:
        _kf = key_fn

        def _inv(*args: Any, **kwargs: Any) -> None:
            store.delete(_kf(*args, **kwargs))

        wrapper.invalidate = _inv
    elif static_key is not None:
        _sk = static_key

        def _inv_static() -> None:  # type: ignore[misc]
            store.delete(_sk)

        wrapper.invalidate = _inv_static

    def _clear() -> None:
        if hasattr(store, "clear"):
            store.clear()  # type: ignore[union-attr]

    wrapper.clear = _clear


# ──────────────────────────────────────────────────────────────────────────────
# @cache
# ──────────────────────────────────────────────────────────────────────────────


def cache(
    ttl: int | float,
    *,
    key: str | Callable[..., str] = "{}",
    stale: int | float = 0,
    store: CacheStorage | Callable[[], CacheStorage] | None = None,
    metrics: MetricsCollector | None = None,
) -> Callable[[F], F]:
    """Cache decorator — works on sync **and** async functions.

    Args:
        ttl:     Time-to-live in seconds.  ``0`` disables caching.
        key:     Key template (``"{user_id}"`` / ``"{}"`` / callable).
                 Defaults to ``"{}"`` — uses the first positional argument.
        stale:   Extra seconds to serve stale data while refreshing in background
                 (stale-while-revalidate pattern).  ``0`` = pure TTL.
        store:   Cache backend.  Pass an instance, a no-arg factory, or ``None``
                 for a private :class:`~storage.InMemCache` per decorated function.
        metrics: Optional :class:`~metrics.MetricsCollector`.

    The decorated function gains three attributes:

    * ``func.store``  — the :class:`~storage.utils.CacheStorage` instance.
    * ``func.invalidate(*args, **kwargs)``  — delete a specific entry.
    * ``func.clear()``  — wipe all entries in this cache.

    Examples::

        @cache(60, key="user:{user_id}")
        async def get_user(user_id: int) -> User: ...

        @cache(30, stale=60, key="prices:{}")
        def get_prices(symbol: str) -> float: ...

        store = RedisCache(redis_client, prefix="myapp:")

        @cache(300, key="cfg", store=store)
        async def get_config() -> dict: ...
    """

    def decorator(func: F) -> F:
        key_fn = _make_key_fn(key, func)
        cache_obj = _resolve_store(store)
        if metrics is not None:
            cache_obj = InstrumentedStorage(
                cache_obj, metrics, func.__name__, {"decorator": "cache"}
            )

        # Bind hot-path callables once at decoration time
        cache_get = cache_obj.get
        cache_set = cache_obj.set
        get_entry = cache_obj.get_entry
        set_entry = cache_obj.set_entry
        set_if_not_exists = cache_obj.set_if_not_exists
        now_fn = time.time
        _stale = stale  # local alias avoids closure-cell lookup

        if asyncio.iscoroutinefunction(func):
            if _stale > 0:
                # ── async SWR ────────────────────────────────────────────────
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    if ttl <= 0:
                        return await func(*args, **kwargs)

                    cache_key = key_fn(*args, **kwargs)
                    now = now_fn()
                    entry = get_entry(cache_key, now)

                    if entry is not None:
                        if now < entry.fresh_until:
                            return entry.value
                        if (now - entry.created_at) <= (ttl + _stale):
                            lock_key = f"{cache_key}:r"
                            if set_if_not_exists(lock_key, "1", _stale or 10):

                                async def _bg_refresh() -> None:
                                    try:
                                        new_val = await func(*args, **kwargs)
                                        t = now_fn()
                                        set_entry(
                                            cache_key,
                                            CacheEntry(
                                                value=new_val,
                                                fresh_until=t + ttl,
                                                created_at=t,
                                            ),
                                        )
                                    except Exception:
                                        logger.exception(
                                            "SWR refresh failed for %r", cache_key
                                        )

                                asyncio.create_task(_bg_refresh())
                            return entry.value

                    result = await func(*args, **kwargs)
                    t = now_fn()
                    set_entry(
                        cache_key,
                        CacheEntry(value=result, fresh_until=t + ttl, created_at=t),
                    )
                    return result

            else:
                # ── async TTL-only (no SWR) — use cache_get for one fewer time.time() ──
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
                    if ttl <= 0:
                        return await func(*args, **kwargs)

                    cache_key = key_fn(*args, **kwargs)
                    value = cache_get(cache_key)
                    if value is not None:
                        return value

                    result = await func(*args, **kwargs)
                    cache_set(cache_key, result, ttl)
                    return result

            _attach(async_wrapper, func, store=cache_obj, key_fn=key_fn)
            return async_wrapper  # type: ignore[return-value]

        # ── sync ──────────────────────────────────────────────────────────────

        if _stale > 0:
            # ── sync SWR ─────────────────────────────────────────────────────
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                if ttl <= 0:
                    return func(*args, **kwargs)

                cache_key = key_fn(*args, **kwargs)
                now = now_fn()
                entry = get_entry(cache_key, now)

                if entry is not None:
                    if now < entry.fresh_until:
                        return entry.value

                    if (now - entry.created_at) <= (ttl + _stale):
                        lock_key = f"{cache_key}:r"
                        if set_if_not_exists(lock_key, "1", _stale or 10):

                            def _bg_refresh_sync() -> None:
                                try:
                                    new_val = func(*args, **kwargs)
                                    t = now_fn()
                                    set_entry(
                                        cache_key,
                                        CacheEntry(
                                            value=new_val,
                                            fresh_until=t + ttl,
                                            created_at=t,
                                        ),
                                    )
                                except Exception:
                                    logger.exception(
                                        "SWR sync refresh failed for %r", cache_key
                                    )

                            _sched = SharedScheduler.get_scheduler()
                            SharedScheduler.start()
                            _sched.add_job(_bg_refresh_sync)
                        return entry.value

                result = func(*args, **kwargs)
                t = now_fn()
                set_entry(
                    cache_key,
                    CacheEntry(value=result, fresh_until=t + ttl, created_at=t),
                )
                return result

        else:
            # ── sync TTL-only — skip get_entry(); use cache_get (one time.time() call) ──
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:  # type: ignore[misc]
                if ttl <= 0:
                    return func(*args, **kwargs)

                cache_key = key_fn(*args, **kwargs)
                value = cache_get(cache_key)
                if value is not None:
                    return value

                result = func(*args, **kwargs)
                cache_set(cache_key, result, ttl)
                return result

        _attach(sync_wrapper, func, store=cache_obj, key_fn=key_fn)
        return sync_wrapper  # type: ignore[return-value]

    return decorator  # type: ignore[return-value]


# ──────────────────────────────────────────────────────────────────────────────
# @bg — background loader
# ──────────────────────────────────────────────────────────────────────────────


class bg:
    """Background-refresh cache decorator and factory.

    **Loader** — decorate a zero-argument function; it will run on a fixed
    schedule and serve cached results on every call::

        @bg(300, key="config")
        async def load_config():
            return await fetch_config()

        cfg = await load_config()  # returns cached, refreshes in background

    **Writer / Reader** — useful when a single process writes to a shared
    store (e.g. Redis) and many workers read from it locally::

        @bg.write(60, key="prices")
        async def refresh_prices():
            return await fetch_prices()

        get_prices = bg.read("prices", interval=60, store=redis_store)
        prices = get_prices()

    **Shutdown**::

        bg.shutdown()
    """

    _writer_registry: ClassVar[dict[str, Any]] = {}

    def __init__(
        self,
        interval: int,
        *,
        key: str,
        ttl: int | float | None = None,
        store: CacheStorage | Callable[[], CacheStorage] | None = None,
        metrics: MetricsCollector | None = None,
        run_immediately: bool = True,
        on_error: Callable[[Exception], None] | None = None,
    ) -> None:
        self._interval = interval
        self._key = key
        self._ttl = ttl
        self._store = store
        self._metrics = metrics
        self._run_immediately = run_immediately
        self._on_error = on_error

    def __call__(self, func: Callable[[], T]) -> Callable[[], T]:
        return _register_loader(
            func,
            key=self._key,
            interval=self._interval,
            ttl=self._ttl,
            store=self._store,
            metrics=self._metrics,
            run_immediately=self._run_immediately,
            on_error=self._on_error,
        )

    @classmethod
    def write(
        cls,
        interval: int,
        *,
        key: str,
        ttl: int | float | None = None,
        store: CacheStorage | Callable[[], CacheStorage] | None = None,
        metrics: MetricsCollector | None = None,
        on_error: Callable[[Exception], None] | None = None,
        run_immediately: bool = True,
    ) -> Callable[[Callable[[], T]], Callable[[], T]]:
        """Register a single writer for a shared cache key.

        Enforces that only one writer exists per key across the process.
        Readers created with :meth:`read` and the same *key* will automatically
        use this writer's store when *store* is omitted.
        """

        def decorator(func: Callable[[], T]) -> Callable[[], T]:
            return _register_writer(
                func, key, interval, ttl, store, metrics, on_error, run_immediately
            )

        return decorator

    @classmethod
    def read(
        cls,
        key: str,
        *,
        interval: int = 0,
        ttl: int | float | None = None,
        store: CacheStorage | Callable[[], CacheStorage] | None = None,
        metrics: MetricsCollector | None = None,
        on_error: Callable[[Exception], None] | None = None,
        run_immediately: bool = True,
    ) -> Callable[[], T | None]:
        """Create a read-only consumer that pulls from a shared cache.

        The reader keeps a fast local :class:`InMemCache` copy and syncs it
        from *store* on a fixed schedule.

        If *store* is ``None`` **and** a writer was registered for *key* via
        :meth:`write`, the writer's store is used automatically — you never
        need to pass the store twice.

        Multiple readers for the same key each get an independent scheduler
        job so they can run on different intervals without interfering.
        """
        return _get_reader(
            key,
            interval=interval,
            ttl=ttl,
            store=store,
            metrics=metrics,
            on_error=on_error,
            run_immediately=run_immediately,
        )

    @classmethod
    def shutdown(cls, wait: bool = True) -> None:
        """Stop all background schedulers and clear the writer registry."""
        SharedAsyncScheduler.shutdown(wait)
        SharedScheduler.shutdown(wait)
        cls._writer_registry.clear()


# ──────────────────────────────────────────────────────────────────────────────
# Internal helpers — bg internals
# ──────────────────────────────────────────────────────────────────────────────


def _resolve_bg_ttl(ttl: int | float | None, interval: int) -> int | float:
    if interval <= 0:
        return ttl or 0
    return ttl if ttl is not None else interval * 2


def _register_loader(
    func: Callable[[], T],
    *,
    key: str,
    interval: int,
    ttl: int | float | None,
    store: CacheStorage | Callable[[], CacheStorage] | None,
    metrics: MetricsCollector | None,
    run_immediately: bool,
    on_error: Callable[[Exception], None] | None,
) -> Callable[[], T]:
    cache_key = key
    if interval <= 0:
        interval = 0
    effective_ttl: int | float = _resolve_bg_ttl(ttl, interval)

    cache_obj = _resolve_store(store)
    if metrics is not None:
        cache_obj = InstrumentedStorage(
            cache_obj, metrics, cache_key, {"decorator": "bg"}
        )

    cache_get = cache_obj.get
    cache_set = cache_obj.set

    def _handle_error(e: Exception) -> None:
        if on_error:
            try:
                on_error(e)
            except Exception:
                logger.exception("bg on_error handler raised for key %r", cache_key)
        else:
            logger.exception("bg refresh failed for key %r", cache_key)

    if asyncio.iscoroutinefunction(func):
        loader_lock: asyncio.Lock | None = None
        initial_load_done = False
        initial_load_task: asyncio.Task[None] | None = None

        if interval <= 0 or effective_ttl <= 0:

            async def async_passthrough() -> T:
                return await func()

            _attach(async_passthrough, func, store=cache_obj, static_key=cache_key)
            return async_passthrough  # type: ignore[return-value]

        async def async_refresh() -> None:
            t0 = time.monotonic()
            try:
                data = await func()
                cache_set(cache_key, data, effective_ttl)
                if metrics is not None:
                    metrics.record_background_refresh(
                        cache_key,
                        success=True,
                        duration_seconds=time.monotonic() - t0,
                    )
            except Exception as e:
                if metrics is not None:
                    metrics.record_background_refresh(
                        cache_key,
                        success=False,
                        duration_seconds=time.monotonic() - t0,
                    )
                _handle_error(e)

        next_run_time: datetime | None = None
        if run_immediately and cache_get(cache_key) is None:
            try:
                loop = asyncio.get_running_loop()
                initial_load_task = loop.create_task(async_refresh())
                next_run_time = datetime.now() + timedelta(seconds=interval * 2)
            except RuntimeError:
                asyncio.run(async_refresh())
                initial_load_done = True
                next_run_time = datetime.now() + timedelta(seconds=interval * 2)

        sched = SharedAsyncScheduler.get_scheduler()
        SharedAsyncScheduler.ensure_started()
        sched.add_job(
            async_refresh,
            trigger=IntervalTrigger(seconds=interval),
            id=cache_key,
            replace_existing=True,
            next_run_time=next_run_time,
        )

        async def async_wrapper() -> T:
            nonlocal loader_lock, initial_load_done, initial_load_task
            value = cache_get(cache_key)
            if value is not None:
                return value
            if loader_lock is None:
                loader_lock = asyncio.Lock()
            async with loader_lock:
                value = cache_get(cache_key)
                if value is not None:
                    return value
                if not initial_load_done:
                    if initial_load_task is not None:
                        await initial_load_task
                    elif not run_immediately:
                        await async_refresh()
                    initial_load_done = True
                value = cache_get(cache_key)
                if value is not None:
                    return value
                result = await func()
                cache_set(cache_key, result, effective_ttl)
                return result

        _attach(async_wrapper, func, store=cache_obj, static_key=cache_key)
        return async_wrapper  # type: ignore[return-value]

    # ── sync ──────────────────────────────────────────────────────────────────

    sync_lock = _ThreadLock()
    sync_initial_load_done = False

    if interval <= 0 or effective_ttl <= 0:

        def sync_passthrough() -> T:
            return func()

        _attach(sync_passthrough, func, store=cache_obj, static_key=cache_key)
        return sync_passthrough

    def sync_refresh() -> None:
        t0 = time.monotonic()
        try:
            data = func()
            cache_set(cache_key, data, effective_ttl)
            if metrics is not None:
                metrics.record_background_refresh(
                    cache_key,
                    success=True,
                    duration_seconds=time.monotonic() - t0,
                )
        except Exception as e:
            if metrics is not None:
                metrics.record_background_refresh(
                    cache_key,
                    success=False,
                    duration_seconds=time.monotonic() - t0,
                )
            _handle_error(e)

    next_run_time_sync: datetime | None = None
    if run_immediately and cache_get(cache_key) is None:
        sync_refresh()
        sync_initial_load_done = True
        next_run_time_sync = datetime.now() + timedelta(seconds=interval * 2)

    sched_sync = SharedScheduler.get_scheduler()
    SharedScheduler.start()
    sched_sync.add_job(
        sync_refresh,
        trigger=IntervalTrigger(seconds=interval),
        id=cache_key,
        replace_existing=True,
        next_run_time=next_run_time_sync,
    )

    def sync_wrapper() -> T:
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
                    sync_refresh()
                sync_initial_load_done = True
            value = cache_get(cache_key)
            if value is not None:
                return value
            result = func()
            cache_set(cache_key, result, effective_ttl)
            return result

    _attach(sync_wrapper, func, store=cache_obj, static_key=cache_key)
    return sync_wrapper


def _register_writer(
    func: Callable[[], T],
    key: str,
    interval: int,
    ttl: int | float | None,
    store: CacheStorage | Callable[[], CacheStorage] | None,
    metrics: MetricsCollector | None,
    on_error: Callable[[Exception], None] | None,
    run_immediately: bool,
) -> Callable[[], T]:
    cache_key = key
    if cache_key in bg._writer_registry:
        raise ValueError(f"bg writer already registered for key '{cache_key}'")

    if interval <= 0:
        interval = 0
    effective_ttl: int | float = _resolve_bg_ttl(ttl, interval)

    cache_obj = _resolve_store(store)
    if metrics is not None:
        cache_obj = InstrumentedStorage(
            cache_obj, metrics, cache_key, {"decorator": "bg.write"}
        )
    cache_get = cache_obj.get
    cache_set = cache_obj.set

    def _handle_error(e: Exception) -> None:
        if on_error:
            try:
                on_error(e)
            except Exception:
                logger.exception("bg.write on_error raised for key %r", cache_key)
        else:
            logger.exception("bg.write failed for key %r", cache_key)

    if asyncio.iscoroutinefunction(func):
        # Init lock at registration time — never lazily inside the coroutine
        _alock = asyncio.Lock()

        async def _run_once_async() -> T:
            async with _alock:
                try:
                    data = await func()
                    cache_set(cache_key, data, effective_ttl)
                    return data
                except Exception as e:
                    _handle_error(e)
                    raise

        async def _async_refresh() -> None:
            t0 = time.monotonic()
            try:
                await _run_once_async()
                if metrics is not None:
                    metrics.record_background_refresh(
                        cache_key,
                        success=True,
                        duration_seconds=time.monotonic() - t0,
                    )
            except Exception:
                if metrics is not None:
                    metrics.record_background_refresh(
                        cache_key,
                        success=False,
                        duration_seconds=time.monotonic() - t0,
                    )

        next_run_time: datetime | None = None
        if run_immediately and cache_get(cache_key) is None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_async_refresh())
                next_run_time = datetime.now() + timedelta(seconds=interval * 2)
            except RuntimeError:
                asyncio.run(_async_refresh())
                next_run_time = datetime.now() + timedelta(seconds=interval * 2)

        if interval > 0:
            sched = SharedAsyncScheduler.get_scheduler()
            SharedAsyncScheduler.ensure_started()
            sched.add_job(
                _async_refresh,
                trigger=IntervalTrigger(seconds=interval),
                id=cache_key,
                replace_existing=True,
                next_run_time=next_run_time,
            )

        async def writer_async() -> T:
            value = cache_get(cache_key)
            if value is not None:
                return value  # type: ignore[return-value]
            return await _run_once_async()

        _attach(writer_async, func, store=cache_obj, static_key=cache_key)
        bg._writer_registry[cache_key] = {
            "cache": cache_obj,
            "ttl": effective_ttl,
            "wrapper": writer_async,
            "is_async": True,
        }
        return writer_async  # type: ignore[return-value]

    # ── sync writer ───────────────────────────────────────────────────────────

    _slock = _ThreadLock()

    def _run_once_sync() -> T:
        with _slock:
            try:
                data = func()
                cache_set(cache_key, data, effective_ttl)
                return data
            except Exception as e:
                _handle_error(e)
                raise

    def _sync_refresh() -> None:
        t0 = time.monotonic()
        try:
            _run_once_sync()
            if metrics is not None:
                metrics.record_background_refresh(
                    cache_key,
                    success=True,
                    duration_seconds=time.monotonic() - t0,
                )
        except Exception:
            if metrics is not None:
                metrics.record_background_refresh(
                    cache_key,
                    success=False,
                    duration_seconds=time.monotonic() - t0,
                )

    next_run_time_sync: datetime | None = None
    if run_immediately and cache_get(cache_key) is None:
        _sync_refresh()
        next_run_time_sync = datetime.now() + timedelta(seconds=interval * 2)

    if interval > 0:
        sched_s = SharedScheduler.get_scheduler()
        SharedScheduler.start()
        sched_s.add_job(
            _sync_refresh,
            trigger=IntervalTrigger(seconds=interval),
            id=cache_key,
            replace_existing=True,
            next_run_time=next_run_time_sync,
        )

    def writer_sync() -> T:
        value = cache_get(cache_key)
        if value is not None:
            return value  # type: ignore[return-value]
        return _run_once_sync()

    _attach(writer_sync, func, store=cache_obj, static_key=cache_key)
    bg._writer_registry[cache_key] = {
        "cache": cache_obj,
        "ttl": effective_ttl,
        "wrapper": writer_sync,
        "is_async": False,
    }
    return writer_sync


# Reader instance counter — makes every reader's scheduler job ID unique
_reader_counter = 0


def _get_reader(
    key: str,
    *,
    interval: int,
    ttl: int | float | None,
    store: CacheStorage | Callable[[], CacheStorage] | None,
    metrics: MetricsCollector | None,
    on_error: Callable[[Exception], None] | None,
    run_immediately: bool = True,
) -> Callable[[], T | None]:
    global _reader_counter
    _reader_counter += 1
    reader_id = _reader_counter

    cache_key = key

    # ── Auto-discover writer's store when store=None ──────────────────────────
    if store is None:
        writer_entry = bg._writer_registry.get(cache_key)
        if writer_entry is not None:
            source_cache: CacheStorage = writer_entry["cache"]
            if ttl is None:
                ttl = writer_entry["ttl"]
        else:
            logger.debug(
                "bg.read(%r): no writer registered and store=None — reader will always return None",
                cache_key,
            )
            source_cache = InMemCache()
    else:
        source_cache = _resolve_store(store)

    if interval <= 0:
        interval = 0
    effective_ttl: int | float = _resolve_bg_ttl(ttl, interval)

    # Local in-memory mirror — hot path never touches the source store
    local_cache = InMemCache()
    source_get = source_cache.get
    local_get = local_cache.get
    local_set = local_cache.set

    def _handle_error(e: Exception) -> None:
        if on_error:
            try:
                on_error(e)
            except Exception:
                logger.exception("bg.read on_error raised for key %r", cache_key)
        else:
            logger.exception("bg.read refresh failed for key %r", cache_key)

    def _load_once() -> None:
        try:
            value = source_get(cache_key)
            if value is not None:
                local_set(cache_key, value, effective_ttl)
        except Exception as e:
            _handle_error(e)

    if run_immediately and (effective_ttl > 0 or interval > 0):
        _load_once()

    if interval > 0:
        sched = SharedScheduler.get_scheduler()
        SharedScheduler.start()
        # Unique job ID per reader instance — prevents multiple readers from
        # silently overwriting each other's scheduler job.
        sched.add_job(
            _load_once,
            trigger=IntervalTrigger(seconds=interval),
            id=f"reader:{cache_key}:{reader_id}",
            replace_existing=True,
        )

    def reader() -> T | None:
        # Hot path: local InMemCache hit (lock-free, ~0.18 µs)
        value = local_get(cache_key)
        if value is not None:
            return value
        # Cold path: pull from source on first call or after TTL expiry
        _load_once()
        return local_get(cache_key)

    _attach(reader, reader, store=local_cache, static_key=cache_key)
    return reader  # type: ignore[return-value]
