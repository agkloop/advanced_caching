import asyncio
import time
import pytest

from advanced_caching import BGCache, InMemCache


@pytest.mark.asyncio
async def test_single_writer_multi_reader_async_with_fallback():
    calls = {"n": 0}

    shared_cache = InMemCache()

    @BGCache.register_writer(
        "shared", interval_seconds=0.01, run_immediately=True, cache=shared_cache
    )
    async def writer():
        calls["n"] += 1
        return {"value": calls["n"]}

    reader_cache = shared_cache

    reader_a = BGCache.get_reader(
        "shared",
        interval_seconds=0.01,
        ttl=None,
        run_immediately=True,
        cache=reader_cache,
    )
    reader_b = BGCache.get_reader(
        "shared",
        interval_seconds=0.01,
        ttl=None,
        run_immediately=True,
        cache=reader_cache,
    )

    async def wait_for_value(reader, timeout=0.2):
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            val = reader()
            if val is not None:
                return val
            await asyncio.sleep(0.01)
        return None

    v1 = await wait_for_value(reader_a)
    v2 = await wait_for_value(reader_b)
    assert v1 == v2
    assert v1 is not None and v1.get("value", 0) >= 1

    await asyncio.sleep(0.05)
    v3 = await wait_for_value(reader_a)
    assert v3 is not None and v3.get("value", 0) >= v1.get("value", 0)

    BGCache.shutdown()


@pytest.mark.asyncio
async def test_reader_without_fallback_returns_none():
    reader = BGCache.get_reader(
        "missing", interval_seconds=0, ttl=0, run_immediately=False
    )
    assert reader() is None
    BGCache.shutdown()


def test_single_writer_enforced_sync():
    @BGCache.register_writer(
        "only_one", interval_seconds=0.01, run_immediately=False, cache=InMemCache()
    )
    def writer():
        return 1

    with pytest.raises(ValueError):

        @BGCache.register_writer("only_one", interval_seconds=0.01)
        def writer2():
            return 2

    BGCache.shutdown()


@pytest.mark.asyncio
async def test_sync_writer_async_reader_fallback_runs_in_executor():
    calls = {"n": 0}

    shared_cache = InMemCache()

    @BGCache.register_writer(
        "mix", interval_seconds=0.01, ttl=1, run_immediately=False, cache=shared_cache
    )
    def writer_sync():
        calls["n"] += 1
        return calls["n"]

    reader_async = BGCache.get_reader(
        "mix",
        interval_seconds=0.01,
        ttl=1,
        run_immediately=False,
        cache=shared_cache,
    )

    # First call triggers load_once pull from source cache (which is empty at start)
    assert reader_async() is None
    # Populate source via writer
    _ = writer_sync()
    await asyncio.sleep(0.05)

    # Reader should eventually see the value after writer populates source cache.
    async def wait_for_value(reader, timeout=0.5):
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            val = reader()
            if val is not None:
                return val
            await asyncio.sleep(0.01)
        return None

    assert await wait_for_value(reader_async) is not None

    BGCache.shutdown()


@pytest.mark.asyncio
async def test_e2e_async_writer_reader_background_refresh():
    shared_cache = InMemCache()
    calls = {"n": 0}

    @BGCache.register_writer(
        "bg_async",
        interval_seconds=0.05,
        run_immediately=True,
        cache=shared_cache,
    )
    async def writer_async():
        calls["n"] += 1
        return {"count": calls["n"]}

    reader = BGCache.get_reader(
        "bg_async",
        interval_seconds=0.05,
        ttl=None,
        run_immediately=True,
        cache=shared_cache,
    )

    async def wait_for_value(reader, min_count, timeout=0.5):
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            val = reader()
            if val is not None and val.get("count", 0) >= min_count:
                return val
            await asyncio.sleep(0.02)
        return None

    first = await wait_for_value(reader, 1)
    assert first is not None and first.get("count", 0) >= 1

    updated = await wait_for_value(reader, 2)
    assert updated is not None and updated.get("count", 0) >= 2

    BGCache.shutdown()


def test_e2e_sync_writer_reader_background_refresh():
    shared_cache = InMemCache()
    calls = {"n": 0}

    @BGCache.register_writer(
        "bg_sync",
        interval_seconds=0.05,
        run_immediately=True,
        cache=shared_cache,
    )
    def writer_sync():
        calls["n"] += 1
        return {"count": calls["n"]}

    reader = BGCache.get_reader(
        "bg_sync",
        interval_seconds=0.05,
        ttl=None,
        run_immediately=True,
        cache=shared_cache,
    )

    def wait_for_value(reader_fn, min_count, timeout=0.5):
        start = time.time()
        while time.time() - start < timeout:
            val = reader_fn()
            if val is not None and val.get("count", 0) >= min_count:
                return val
            time.sleep(0.02)
        return None

    first = wait_for_value(reader, 1)
    assert first is not None and first.get("count", 0) >= 1

    updated = wait_for_value(reader, 2)
    assert updated is not None and updated.get("count", 0) >= 2
