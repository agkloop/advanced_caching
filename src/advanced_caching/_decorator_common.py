"""Internal helpers shared by caching decorators.

This module is intentionally *not* part of the public API.

Goals:
- Eliminate repeated cache-backend normalization patterns.
- Keep decorator hot paths small by binding frequently-used attributes once.
- Centralize wrapper metadata used by tests/debugging (`__wrapped__`, `_cache`, etc.).
"""

from __future__ import annotations
from typing import Callable, TypeVar

from .storage import CacheStorage, InMemCache

T = TypeVar("T")


def normalize_cache_factory(
    cache: CacheStorage | Callable[[], CacheStorage] | None,
    *,
    default_factory: Callable[[], CacheStorage] = InMemCache,
) -> Callable[[], CacheStorage]:
    """Normalize a cache backend parameter into a no-arg factory.

    Accepted forms:
    - None: use default_factory
    - Callable[[], CacheStorage]: use as-is
    - CacheStorage instance: wrap into a factory that returns the instance

    This keeps decorator code paths small and consistent.
    """

    if cache is None:
        return default_factory
    if callable(cache):
        return cache  # type: ignore[return-value]

    cache_instance = cache

    def factory() -> CacheStorage:
        return cache_instance

    return factory


def attach_wrapper_metadata(
    wrapper: Callable[..., T],
    func: Callable[..., T],
    *,
    cache_obj: CacheStorage,
    cache_key: str | None = None,
) -> None:
    """Attach metadata fields used for debugging/tests.

    Notes:
    - We intentionally avoid functools.wraps() here to keep decoration overhead
      minimal and to preserve existing behavior.
    """

    wrapper.__wrapped__ = func  # type: ignore[attr-defined]
    wrapper.__name__ = func.__name__  # type: ignore[attr-defined]
    wrapper.__doc__ = func.__doc__  # type: ignore[attr-defined]
    wrapper._cache = cache_obj  # type: ignore[attr-defined]
    if cache_key is not None:
        wrapper._cache_key = cache_key  # type: ignore[attr-defined]
