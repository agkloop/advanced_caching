import asyncio
import pytest
from advanced_caching import cache, bg


def test_ttl_sync_remains_sync():
    @cache(60, key="ttl_sync")
    def sync_fn(x):
        return x + 1

    assert not asyncio.iscoroutinefunction(sync_fn)
    assert sync_fn(1) == 2


@pytest.mark.asyncio
async def test_ttl_async_remains_async():
    @cache(60, key="ttl_async")
    async def async_fn(x):
        return x + 1

    assert asyncio.iscoroutinefunction(async_fn)
    assert await async_fn(1) == 2


def test_swr_sync_remains_sync():
    @cache(60, key="swr_sync")
    def sync_fn(x):
        return x + 1

    assert not asyncio.iscoroutinefunction(sync_fn)
    assert sync_fn(1) == 2


@pytest.mark.asyncio
async def test_swr_async_remains_async():
    @cache(60, key="swr_async")
    async def async_fn(x):
        return x + 1

    assert asyncio.iscoroutinefunction(async_fn)
    assert await async_fn(1) == 2


def test_bg_sync_remains_sync():
    @bg(60, key="bg_sync")
    def sync_loader():
        return 42

    assert not asyncio.iscoroutinefunction(sync_loader)
    assert sync_loader() == 42
    bg.shutdown()


@pytest.mark.asyncio
async def test_bg_async_remains_async():
    @bg(60, key="bg_async")
    async def async_loader():
        return 42

    assert asyncio.iscoroutinefunction(async_loader)
    assert await async_loader() == 42
    bg.shutdown()
