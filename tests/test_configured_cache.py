import pytest
import time
from advanced_caching import cache, bg, InMemCache


def test_configured_ttl_cache():
    custom_cache = InMemCache()
    call_count = 0

    @cache(60, key="key", store=custom_cache)
    def func():
        nonlocal call_count
        call_count += 1
        return 1

    assert func() == 1
    assert call_count == 1
    assert custom_cache.exists("key")

    # Should hit cache
    assert func() == 1
    assert call_count == 1


def test_configured_swr_cache():
    custom_cache = InMemCache()
    call_count = 0

    @cache(60, key="swr", store=custom_cache)
    def func():
        nonlocal call_count
        call_count += 1
        return 2

    assert func() == 2
    assert call_count == 1
    assert custom_cache.exists("swr")

    # Should hit cache
    assert func() == 2
    assert call_count == 1


def test_configured_bg_cache():
    custom_cache = InMemCache()
    call_count = 0

    @bg(60, key="bg", run_immediately=True, store=custom_cache)
    def func():
        nonlocal call_count
        call_count += 1
        return 3

    # First call might trigger load if run_immediately=True logic works synchronously for sync functions
    # In decorators.py, sync wrapper checks cache, if miss, calls loader.

    assert func() == 3
    assert call_count >= 1
    assert custom_cache.exists("bg")
