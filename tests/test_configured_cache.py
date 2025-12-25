import pytest
import time
from advanced_caching import TTLCache, SWRCache, BGCache, InMemCache


def test_configured_ttl_cache():
    cache = InMemCache()
    MyTTL = TTLCache.configure(cache=cache)

    call_count = 0

    @MyTTL.cached("key", ttl=60)
    def func():
        nonlocal call_count
        call_count += 1
        return 1

    assert func() == 1
    assert call_count == 1
    assert cache.exists("key")

    # Should hit cache
    assert func() == 1
    assert call_count == 1


def test_configured_swr_cache():
    cache = InMemCache()
    MySWR = SWRCache.configure(cache=cache)

    call_count = 0

    @MySWR.cached("swr", ttl=60)
    def func():
        nonlocal call_count
        call_count += 1
        return 2

    assert func() == 2
    assert call_count == 1
    assert cache.exists("swr")

    # Should hit cache
    assert func() == 2
    assert call_count == 1


def test_configured_bg_cache():
    cache = InMemCache()
    MyBG = BGCache.configure(cache=cache)

    call_count = 0

    @MyBG.register_loader("bg", interval_seconds=60, run_immediately=True)
    def func():
        nonlocal call_count
        call_count += 1
        return 3

    # First call might trigger load if run_immediately=True logic works synchronously for sync functions
    # In decorators.py, sync wrapper checks cache, if miss, calls loader.

    assert func() == 3
    assert call_count >= 1
    assert cache.exists("bg")
