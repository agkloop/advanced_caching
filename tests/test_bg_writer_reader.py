import asyncio
import time
import pytest

from advanced_caching import bg, InMemCache, InMemoryMetrics


@pytest.mark.asyncio
async def test_single_writer_multi_reader_async_with_fallback():
    calls = {"n": 0}

    shared_cache = InMemCache()

    @bg.write(0.01, key="shared", run_immediately=True, store=shared_cache)
    async def writer():
        calls["n"] += 1
        return {"value": calls["n"]}

    reader_a = bg.read(
        "shared", interval=0.01, ttl=None, run_immediately=True, store=shared_cache
    )
    reader_b = bg.read(
        "shared", interval=0.01, ttl=None, run_immediately=True, store=shared_cache
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

    bg.shutdown()


@pytest.mark.asyncio
async def test_reader_without_fallback_returns_none():
    reader = bg.read("missing_key_xyz", interval=0, ttl=0, run_immediately=False)
    assert reader() is None
    bg.shutdown()


def test_single_writer_enforced_sync():
    @bg.write(0.01, key="only_one", run_immediately=False, store=InMemCache())
    def writer():
        return 1

    with pytest.raises(ValueError):

        @bg.write(0.01, key="only_one")
        def writer2():
            return 2

    bg.shutdown()


@pytest.mark.asyncio
async def test_sync_writer_async_reader_fallback_runs_in_executor():
    calls = {"n": 0}
    shared_cache = InMemCache()

    @bg.write(0.01, key="mix", ttl=1, run_immediately=False, store=shared_cache)
    def writer_sync():
        calls["n"] += 1
        return calls["n"]

    reader_async = bg.read(
        "mix", interval=0.01, ttl=1, run_immediately=False, store=shared_cache
    )

    assert reader_async() is None
    _ = writer_sync()
    await asyncio.sleep(0.05)

    async def wait_for_value(reader, timeout=0.5):
        start = asyncio.get_event_loop().time()
        while asyncio.get_event_loop().time() - start < timeout:
            val = reader()
            if val is not None:
                return val
            await asyncio.sleep(0.01)
        return None

    assert await wait_for_value(reader_async) is not None
    bg.shutdown()


@pytest.mark.asyncio
async def test_e2e_async_writer_reader_background_refresh():
    shared_cache = InMemCache()
    calls = {"n": 0}

    @bg.write(0.05, key="bg_async", run_immediately=True, store=shared_cache)
    async def writer_async():
        calls["n"] += 1
        return {"count": calls["n"]}

    reader = bg.read(
        "bg_async", interval=0.05, ttl=None, run_immediately=True, store=shared_cache
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

    bg.shutdown()


def test_e2e_sync_writer_reader_background_refresh():
    shared_cache = InMemCache()
    calls = {"n": 0}

    @bg.write(0.05, key="bg_sync", run_immediately=True, store=shared_cache)
    def writer_sync():
        calls["n"] += 1
        return {"count": calls["n"]}

    reader = bg.read(
        "bg_sync", interval=0.05, ttl=None, run_immediately=True, store=shared_cache
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


def test_multiple_readers_independent_local_caches():
    """Each bg.read() call creates an independent local mirror cache."""
    shared = InMemCache()
    shared.set("ikey", {"v": 1}, ttl=3600)

    r1 = bg.read("ikey", interval=1, store=shared)
    r2 = bg.read("ikey", interval=1, store=shared)

    assert r1.store is not r2.store, "each reader must have its own local cache"
    assert r1() == r2() == {"v": 1}

    bg.shutdown()


def test_reader_auto_discovers_writer_store():
    """bg.read(key) with store=None uses the writer's store automatically."""
    writer_store = InMemCache()
    writer_store.set("autodisco_key", {"payload": "hello"}, ttl=3600)

    @bg.write(60, key="autodisco_key", store=writer_store, run_immediately=False)
    def noop_writer():
        return {}

    reader = bg.read("autodisco_key")  # no store= → auto-discover
    assert reader() == {"payload": "hello"}

    bg.shutdown()


def test_writer_metrics_record_background_refresh():
    """bg.write with metrics= tracks successful background refreshes."""
    metrics = InMemoryMetrics()
    store = InMemCache()
    calls = {"n": 0}

    @bg.write(
        0.05, key="metered_writer", store=store, metrics=metrics, run_immediately=True
    )
    def refresh():
        calls["n"] += 1
        return {"count": calls["n"]}

    time.sleep(0.15)

    stats = metrics.get_stats()
    bg_stats = stats.get("background_refresh", {})
    assert "metered_writer" in bg_stats
    assert bg_stats["metered_writer"]["success"] >= 1

    bg.shutdown()


def test_writer_metrics_on_error():
    """bg.write with metrics= tracks failed refreshes."""
    metrics = InMemoryMetrics()

    @bg.write(0.05, key="failing_writer", metrics=metrics, run_immediately=True)
    def bad_writer():
        raise RuntimeError("intentional")

    time.sleep(0.15)

    stats = metrics.get_stats()
    bg_stats = stats.get("background_refresh", {})
    assert "failing_writer" in bg_stats
    assert bg_stats["failing_writer"]["failure"] >= 1

    bg.shutdown()
