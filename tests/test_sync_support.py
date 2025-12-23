import asyncio
import pytest
from advanced_caching import TTLCache, SWRCache, BGCache


def test_ttl_sync_remains_sync():
    @TTLCache.cached("ttl_sync", ttl=60)
    def sync_fn(x):
        return x + 1

    assert not asyncio.iscoroutinefunction(sync_fn)
    assert sync_fn(1) == 2


@pytest.mark.asyncio
async def test_ttl_async_remains_async():
    @TTLCache.cached("ttl_async", ttl=60)
    async def async_fn(x):
        return x + 1

    assert asyncio.iscoroutinefunction(async_fn)
    assert await async_fn(1) == 2


def test_swr_sync_remains_sync():
    @SWRCache.cached("swr_sync", ttl=60)
    def sync_fn(x):
        return x + 1

    assert not asyncio.iscoroutinefunction(sync_fn)
    assert sync_fn(1) == 2


@pytest.mark.asyncio
async def test_swr_async_remains_async():
    @SWRCache.cached("swr_async", ttl=60)
    async def async_fn(x):
        return x + 1

    assert asyncio.iscoroutinefunction(async_fn)
    assert await async_fn(1) == 2


def test_bg_sync_remains_sync():
    @BGCache.register_loader("bg_sync", interval_seconds=60)
    def sync_loader():
        return 42

    assert not asyncio.iscoroutinefunction(sync_loader)
    assert sync_loader() == 42
    BGCache.shutdown()


@pytest.mark.asyncio
async def test_bg_async_remains_async():
    @BGCache.register_loader("bg_async", interval_seconds=60)
    async def async_loader():
        return 42

    assert asyncio.iscoroutinefunction(async_loader)
    assert await async_loader() == 42
    BGCache.shutdown()
