"""
Fast and reliable unit tests for caching decorators (Async-first).
Tests TTLCache, SWRCache, and BGCache functionality.
"""

import asyncio
import pytest
import pytest_asyncio
import time

from advanced_caching import (
    BGCache,
    InMemCache,
    TTLCache,
    SWRCache,
    HybridCache,
    validate_cache_storage,
)


@pytest_asyncio.fixture
async def cleanup():
    """Clean up scheduler between tests."""
    yield
    try:
        BGCache.shutdown(wait=False)
    except:
        pass
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
@pytest.mark.usefixtures("cleanup")
class TestTTLCache:
    """TTLCache decorator tests."""

    async def test_basic_caching(self):
        """Test basic TTL caching with function calls."""
        call_count = {"count": 0}

        @TTLCache.cached("user:{}", ttl=60)
        async def get_user(user_id):
            call_count["count"] += 1
            return {"id": user_id, "name": f"User{user_id}"}

        # First call - cache miss
        result1 = await get_user(1)
        assert result1 == {"id": 1, "name": "User1"}
        assert call_count["count"] == 1

        # Second call - cache hit
        result2 = await get_user(1)
        assert result2 == {"id": 1, "name": "User1"}
        assert call_count["count"] == 1  # Not incremented

        # Different key - cache miss
        result3 = await get_user(2)
        assert result3 == {"id": 2, "name": "User2"}
        assert call_count["count"] == 2

    async def test_ttl_expiration(self):
        """Test that cache expires after TTL."""
        call_count = {"count": 0}

        @TTLCache.cached("data:{}", ttl=0.2)
        async def get_data(key):
            call_count["count"] += 1
            return {"key": key, "count": call_count["count"]}

        # First call
        result1 = await get_data("test")
        assert result1["count"] == 1
        assert call_count["count"] == 1

        # Cache should still be valid
        result2 = await get_data("test")
        assert result2["count"] == 1
        assert call_count["count"] == 1

        # Wait for expiration
        await asyncio.sleep(0.3)

        # Cache should be expired, function called again
        result3 = await get_data("test")
        assert result3["count"] == 2
        assert call_count["count"] == 2

    async def test_custom_cache_backend(self):
        """Test TTLCache with custom backend."""
        custom_cache = InMemCache()

        @TTLCache.cached("item:{}", ttl=60, cache=custom_cache)
        async def get_item(item_id):
            return {"id": item_id}

        result = await get_item(123)
        assert result == {"id": 123}

        # Verify in custom cache
        assert custom_cache.exists("item:123")

    async def test_callable_key_function(self):
        """Test TTLCache with callable key function."""

        @TTLCache.cached(key=lambda user_id: f"user:{user_id}", ttl=60)
        async def get_user(user_id):
            return {"id": user_id}

        result = await get_user(42)
        assert result == {"id": 42}

    async def test_isolated_caches(self):
        """Test that each TTL cached function has its own cache."""

        @TTLCache.cached("user:{}", ttl=60)
        async def get_user(user_id):
            return {"type": "user", "id": user_id}

        @TTLCache.cached("product:{}", ttl=60)
        async def get_product(product_id):
            return {"type": "product", "id": product_id}

        # Each should have its own cache
        assert get_user._cache is not get_product._cache

        # Both should work
        assert (await get_user(1))["type"] == "user"
        assert (await get_product(1))["type"] == "product"


@pytest.mark.asyncio
class TestSWRCache:
    """SWRCache (Stale-While-Revalidate) tests."""

    async def test_fresh_cache_hit(self):
        """Test SWR with fresh cache returns immediately."""
        call_count = {"count": 0}

        @SWRCache.cached("user:{}", ttl=60, stale_ttl=30)
        async def get_user(user_id):
            call_count["count"] += 1
            return {"id": user_id, "count": call_count["count"]}

        # First call - cache miss
        result1 = await get_user(1)
        assert result1["count"] == 1
        assert call_count["count"] == 1

        # Second call - should hit fresh cache
        result2 = await get_user(1)
        assert result2["count"] == 1  # Same cached value
        assert call_count["count"] == 1  # Function not called again

    async def test_stale_with_background_refresh(self):
        """Test SWR serves stale data while refreshing in background."""
        call_count = {"count": 0}

        @SWRCache.cached("data:{}", ttl=0.2, stale_ttl=0.5)
        async def get_data(key):
            call_count["count"] += 1
            return {"key": key, "count": call_count["count"]}

        # First call
        result1 = await get_data("test")
        assert result1["count"] == 1
        assert call_count["count"] == 1

        # Wait for data to become stale but within grace period
        await asyncio.sleep(0.3)

        # Should return stale value and refresh in background
        result2 = await get_data("test")
        assert result2["count"] == 1  # Still getting stale data

        # Wait for background refresh to complete
        await asyncio.sleep(0.2)

        # Now should have fresh data
        result3 = await get_data("test")
        assert result3["count"] >= 2  # Should be refreshed

    async def test_too_stale_refetch(self):
        """Test SWR refetches when too stale."""
        call_count = {"count": 0}

        @SWRCache.cached("data:{}", ttl=0.1, stale_ttl=0.1)
        async def get_data(key):
            call_count["count"] += 1
            return {"key": key, "count": call_count["count"]}

        # First call
        result1 = await get_data("test")
        assert result1["count"] == 1

        # Wait until beyond TTL + stale_ttl
        await asyncio.sleep(0.3)

        # Should refetch immediately (not within grace period)
        result2 = await get_data("test")
        assert result2["count"] == 2  # Refetched
        assert call_count["count"] == 2

    async def test_custom_cache_backend(self):
        """Test SWRCache with custom backend."""
        custom_cache = InMemCache()

        @SWRCache.cached("item:{}", ttl=60, stale_ttl=30, cache=custom_cache)
        async def get_item(item_id):
            return {"id": item_id}

        result = await get_item(123)
        assert result == {"id": 123}


@pytest.mark.asyncio
@pytest.mark.usefixtures("cleanup")
class TestBGCache:
    """BGCache (Background Scheduler) tests."""

    async def test_async_loader_immediate(self):
        """Test async loader with immediate execution."""
        call_count = {"count": 0}

        @BGCache.register_loader(
            "async_test", interval_seconds=10, run_immediately=True
        )
        async def load_data():
            call_count["count"] += 1
            return {"value": call_count["count"]}

        await asyncio.sleep(0.1)  # Wait for initial load

        # First call should return cached data
        result = await load_data()
        assert result == {"value": 1}
        assert call_count["count"] == 1

        # Second call should still use cache
        result2 = await load_data()
        assert result2 == {"value": 1}
        assert call_count["count"] == 1  # Not called again

    async def test_sync_loader_no_immediate(self):
        """Test sync loader without immediate execution."""
        call_count = {"count": 0}

        @BGCache.register_loader(
            "no_immediate", interval_seconds=10, run_immediately=False
        )
        def load_data():
            call_count["count"] += 1
            return {"value": call_count["count"]}

        await asyncio.sleep(0.1)

        # Should not have been called yet
        assert call_count["count"] == 0

        # First call will execute the function since cache is empty
        result = load_data()
        assert result == {"value": 1}
        assert call_count["count"] == 1

    async def test_custom_cache_backend(self):
        """Test BGCache using custom cache backend."""
        custom_cache = InMemCache()

        @BGCache.register_loader(
            "custom", interval_seconds=10, run_immediately=True, cache=custom_cache
        )
        async def load_data():
            return {"custom": True}

        await asyncio.sleep(0.1)

        # Verify data is in custom cache
        cached_value = custom_cache.get("custom")
        assert cached_value == {"custom": True}

        # Call function
        result = await load_data()
        assert result == {"custom": True}

    async def test_isolated_cache_instances(self):
        """Test that each loader has its own cache."""

        @BGCache.register_loader("loader1", interval_seconds=10, run_immediately=True)
        async def load1():
            return {"id": 1}

        @BGCache.register_loader("loader2", interval_seconds=10, run_immediately=True)
        async def load2():
            return {"id": 2}

        await asyncio.sleep(0.1)

        # Each should have its own cache
        assert load1._cache is not load2._cache
        assert load1._cache_key == "loader1"
        assert load2._cache_key == "loader2"

        # Each should have correct data
        assert (await load1()) == {"id": 1}
        assert (await load2()) == {"id": 2}

    async def test_error_handling(self):
        """Test error handler is called on failure."""
        errors = []
        error_event = asyncio.Event()

        def error_handler(e):
            errors.append(e)
            error_event.set()

        @BGCache.register_loader(
            "error_test",
            interval_seconds=10,
            run_immediately=True,
            on_error=error_handler,
        )
        async def load_data():
            raise ValueError("Test error")

        try:
            await asyncio.wait_for(error_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("Error handler was not called within 1s")

        # Error should have been captured
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert str(errors[0]) == "Test error"

    async def test_periodic_refresh(self):
        """Test that data refreshes periodically."""
        call_count = {"count": 0}
        load_event = asyncio.Event()

        @BGCache.register_loader("periodic", interval_seconds=0.1, run_immediately=True)
        async def load_data():
            call_count["count"] += 1
            load_event.set()
            return {"value": call_count["count"]}

        # Wait for initial load
        try:
            await asyncio.wait_for(load_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("Initial load did not complete within 1s")

        assert call_count["count"] >= 1
        initial_count = call_count["count"]

        # Wait for one refresh
        load_event.clear()
        try:
            await asyncio.wait_for(load_event.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            pytest.fail("Periodic refresh did not occur within 1s")

        assert call_count["count"] > initial_count

        # Get updated data
        result = await load_data()
        assert result["value"] >= 2

    async def test_multiple_loaders(self):
        """Test multiple loaders can coexist."""

        @BGCache.register_loader("loader_a", interval_seconds=10, run_immediately=True)
        async def load_a():
            return {"name": "a"}

        @BGCache.register_loader("loader_b", interval_seconds=10, run_immediately=True)
        async def load_b():
            return {"name": "b"}

        @BGCache.register_loader("loader_c", interval_seconds=10, run_immediately=True)
        async def load_c():
            return {"name": "c"}

        await asyncio.sleep(0.15)

        # All should work independently
        assert (await load_a())["name"] == "a"
        assert (await load_b())["name"] == "b"
        assert (await load_c())["name"] == "c"

    async def test_lambda_cache_factory(self):
        """Test BGCache with lambda returning HybridCache."""
        call_count = {"count": 0}

        @BGCache.register_loader(
            "test_lambda_cache",
            interval_seconds=3600,
            run_immediately=True,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(), l2_cache=InMemCache(), l1_ttl=60
            ),
        )
        async def get_test_data() -> dict[str, str]:
            call_count["count"] += 1
            return {"key": "value", "count": str(call_count["count"])}

        # First call should hit the cache (run_immediately=True loaded it)
        result1 = await get_test_data()
        assert result1 == {"key": "value", "count": "1"}
        assert call_count["count"] == 1

        # Second call should return cached value
        result2 = await get_test_data()
        assert result2 == {"key": "value", "count": "1"}
        assert call_count["count"] == 1  # No additional call

        # Verify cache object was created correctly
        assert hasattr(get_test_data, "_cache")
        assert get_test_data._cache is not None
        assert isinstance(get_test_data._cache, HybridCache)


@pytest.mark.asyncio
@pytest.mark.usefixtures("cleanup")
class TestCachePerformance:
    """Performance and speed tests."""

    async def test_cache_hit_speed(self):
        """Test that cache hits are fast."""

        @BGCache.register_loader("perf_test", interval_seconds=10, run_immediately=True)
        async def load_data():
            await asyncio.sleep(0.01)  # Simulate slow operation
            return {"data": "value"}

        await asyncio.sleep(0.05)  # Wait for initial load

        # Measure cache hit time
        start = time.perf_counter()
        for _ in range(1000):
            result = await load_data()
        duration = time.perf_counter() - start

        # Should be very fast (<1ms per call on average)
        avg_time = duration / 1000
        assert avg_time < 0.001, f"Cache hit too slow: {avg_time * 1000:.3f}ms"
        assert result == {"data": "value"}

    async def test_ttl_cache_hit_speed(self):
        """Test TTLCache hit speed."""

        @TTLCache.cached("item:{}", ttl=60)
        async def get_item(item_id):
            await asyncio.sleep(0.001)  # Simulate work
            return {"id": item_id}

        # Prime cache
        await get_item(1)

        # Measure cache hits
        start = time.perf_counter()
        for _ in range(1000):
            await get_item(1)
        duration = time.perf_counter() - start

        avg_time = duration / 1000
        assert avg_time < 0.0005, f"TTL cache hit too slow: {avg_time * 1000:.3f}ms"


@pytest.mark.asyncio
class TestKeyTemplates:
    """Key template behavior for TTLCache and SWRCache."""

    async def test_ttl_positional_template(self):
        calls = {"n": 0}

        @TTLCache.cached("user:{}", ttl=60)
        async def get_user(user_id: int):
            calls["n"] += 1
            return {"id": user_id}

        assert (await get_user(1)) == {"id": 1}
        assert (await get_user(1)) == {"id": 1}
        assert calls["n"] == 1  # cache hit by positional template

    async def test_ttl_named_template(self):
        calls = {"n": 0}

        @TTLCache.cached("user:{user_id}", ttl=60)
        async def get_user(*, user_id: int):
            calls["n"] += 1
            return {"id": user_id}

        assert (await get_user(user_id=2)) == {"id": 2}
        assert (await get_user(user_id=2)) == {"id": 2}
        assert calls["n"] == 1

    async def test_swr_default_arg_with_key_function(self):
        calls = {"n": 0}

        @SWRCache.cached(
            key=lambda *a, **k: f"i18n:all:{k.get('lang', a[0] if a else 'en')}",
            ttl=5,
            stale_ttl=10,
        )
        async def load_all(lang: str = "en") -> dict:
            calls["n"] += 1
            return {"hello": f"Hello in {lang}"}

        # Default arg used (no args provided)
        r1 = await load_all()
        r2 = await load_all(lang="en")
        r3 = await load_all()
        assert r1 == {"hello": "Hello in en"}
        assert r2 == {"hello": "Hello in en"}
        assert r3 == {"hello": "Hello in en"}
        assert calls["n"] == 1  # all share the same cache key

    async def test_swr_named_template_with_kwargs(self):
        calls = {"n": 0}

        @SWRCache.cached("i18n:{lang}", ttl=5, stale_ttl=10)
        async def load_i18n(*, lang: str = "en") -> dict:
            calls["n"] += 1
            return {"hello": f"Hello in {lang}"}

        r1 = await load_i18n(lang="en")
        r2 = await load_i18n(lang="en")
        assert r1 == {"hello": "Hello in en"}
        assert r2 == {"hello": "Hello in en"}
        assert calls["n"] == 1

    async def test_swr_positional_template_with_args(self):
        calls = {"n": 0}

        @SWRCache.cached("i18n:{}", ttl=5, stale_ttl=10)
        async def load_i18n(lang: str) -> dict:
            calls["n"] += 1
            return {"hello": f"Hello in {lang}"}

        r1 = await load_i18n("en")
        r2 = await load_i18n("en")
        assert r1 == {"hello": "Hello in en"}
        assert r2 == {"hello": "Hello in en"}
        assert calls["n"] == 1

    async def test_swr_named_template_with_extra_kwargs(self):
        calls = {"n": 0}

        @SWRCache.cached("i18n:{lang}", ttl=5, stale_ttl=10)
        async def load_i18n(lang: str, region: str | None = None) -> dict:
            calls["n"] += 1
            suffix = f"-{region}" if region else ""
            return {"hello": f"Hello in {lang}{suffix}"}

        r1 = await load_i18n(lang="en", region="US")
        r2 = await load_i18n(lang="en", region="US")
        assert r1 == {"hello": "Hello in en-US"}
        assert r2 == {"hello": "Hello in en-US"}
        assert calls["n"] == 1


class TestStorageEdgeCases:
    """Edge cases for storage backends to improve coverage."""

    def test_inmem_cleanup_and_lock_property(self):
        cache = InMemCache()
        cache.set("a", 1, ttl=0)
        cache.set("b", 2, ttl=0)
        # No entries should be expired yet (infinite TTL)
        assert cache.cleanup_expired() == 0
        assert cache.lock is cache.lock  # property returns same lock

    def test_inmem_set_if_not_exists_with_expired_entry(self, monkeypatch):
        cache = InMemCache()
        cache.set("k", "v", ttl=1)
        # Force fresh_until in the past
        entry = cache.get_entry("k")
        assert entry is not None
        entry.fresh_until = time.time() - 10
        cache.set_entry("k", entry)
        assert cache.set_if_not_exists("k", "v2", ttl=1) is True
        assert cache.get("k") == "v2"

    def test_validate_cache_storage_false(self):
        class NotACache:
            def get(self, key):
                return None

        assert validate_cache_storage(NotACache()) is False

    def test_hybridcache_requires_l2(self):
        with pytest.raises(ValueError):
            HybridCache(l2_cache=None)

    def test_hybridcache_basic_flow(self):
        # Use InMemCache as fake L2
        l2 = InMemCache()
        cache = HybridCache(l1_cache=None, l2_cache=l2, l1_ttl=1)
        cache.set("x", 123, ttl=10)
        # First get hits L1 directly
        assert cache.get("x") == 123
        # Exists must be true
        assert cache.exists("x") is True
        # set_if_not_exists should fail because key exists in L2
        assert cache.set_if_not_exists("x", 456, ttl=10) is False
        # Delete removes from both
        cache.delete("x")
        assert cache.get("x") is None


@pytest.mark.asyncio
class TestDecoratorKeyEdgeCases:
    """Exercise edge key-generation paths for decorators."""

    async def test_ttl_key_without_placeholders(self):
        calls = {"n": 0}

        @TTLCache.cached("static-key", ttl=60)
        async def f(user_id: int):
            calls["n"] += 1
            return user_id

        assert (await f(1)) == 1
        assert (await f(2)) == 1  # same key, result from first call
        assert calls["n"] == 1

    async def test_swr_key_without_args_or_kwargs(self):
        calls = {"n": 0}

        @SWRCache.cached("static", ttl=1, stale_ttl=1)
        async def f() -> int:
            calls["n"] += 1
            return calls["n"]

        # First call: miss
        assert (await f()) == 1
        # Immediate second call: hit
        assert (await f()) == 1
        assert calls["n"] == 1

    async def test_swr_key_template_single_kwarg_positional_fallback(self):
        calls = {"n": 0}

        # Template with positional placeholder but only kwarg passed
        @SWRCache.cached("foo:{}", ttl=1, stale_ttl=1)
        async def f(*, x: int) -> int:
            calls["n"] += 1
            return x

        assert (await f(x=1)) == 1
        assert (await f(x=1)) == 1
        assert calls["n"] == 1

    async def test_swr_invalid_format_falls_back_to_raw_key(self):
        calls = {"n": 0}

        # Template expects named field that is never provided; we only pass kwargs
        @SWRCache.cached("foo:{missing}", ttl=1, stale_ttl=1)
        async def f(*, x: int) -> int:
            calls["n"] += 1
            return x

        # First call populates cache with raw key "foo:{missing}" after format failure
        assert (await f(x=1)) == 1
        # Second call uses same raw key and returns cached value despite different arg
        assert (await f(x=2)) == 1
        assert calls["n"] == 1


class TestHybridCache:
    """Test HybridCache L1+L2 behavior with l2_ttl."""

    def test_l2_ttl_defaults_to_l1_ttl_times_2(self):
        """Test that l2_ttl defaults to l1_ttl * 2."""
        l1 = InMemCache()
        l2 = InMemCache()

        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=60)
        assert cache.l1_ttl == 60
        assert cache.l2_ttl == 120

    def test_l2_ttl_explicit_value(self):
        """Test that explicit l2_ttl is respected."""
        l1 = InMemCache()
        l2 = InMemCache()

        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=60, l2_ttl=300)
        assert cache.l1_ttl == 60
        assert cache.l2_ttl == 300

    def test_set_respects_l2_ttl(self):
        """Test that set() uses l2_ttl for L2 cache."""
        l1 = InMemCache()
        l2 = InMemCache()

        # Set l1_ttl=1, l2_ttl=10
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=1, l2_ttl=10)

        cache.set("key1", "value1", ttl=100)

        # Both should have the value immediately
        assert cache.get("key1") == "value1"
        assert l1.get("key1") == "value1"
        assert l2.get("key1") == "value1"

        # Wait for L1 to expire (l1_ttl=1)
        time.sleep(1.2)

        # L1 should be expired, but L2 should still have it
        assert l1.get("key1") is None
        assert l2.get("key1") == "value1"

        # HybridCache should fetch from L2 and repopulate L1
        assert cache.get("key1") == "value1"
        assert l1.get("key1") == "value1"  # L1 repopulated

    def test_set_entry_respects_l2_ttl(self):
        """Test that set_entry() uses l2_ttl for L2 cache."""
        from advanced_caching.storage import CacheEntry

        l1 = InMemCache()
        l2 = InMemCache()

        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=1, l2_ttl=10)

        now = time.time()
        entry = CacheEntry(value="test_value", fresh_until=now + 100, created_at=now)

        cache.set_entry("key2", entry, ttl=100)

        # Both should have the entry (using get() which checks freshness)
        assert cache.get("key2") == "test_value"
        assert l1.get("key2") == "test_value"
        assert l2.get("key2") == "test_value"

        # Wait for L1 to expire (l1_ttl=1)
        time.sleep(1.2)

        # L1 expired (get() returns None for expired), L2 should still have it
        assert l1.get("key2") is None
        assert l2.get("key2") == "test_value"

        # HybridCache should fetch from L2
        assert cache.get("key2") == "test_value"

    def test_set_if_not_exists_respects_l2_ttl(self):
        """Test that set_if_not_exists() uses l2_ttl for L2 cache."""
        l1 = InMemCache()
        l2 = InMemCache()

        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=1, l2_ttl=10)

        # First set should succeed
        assert cache.set_if_not_exists("key3", "value3", ttl=100) is True
        assert cache.get("key3") == "value3"

        # Second set should fail (key exists)
        assert cache.set_if_not_exists("key3", "value3_new", ttl=100) is False
        assert cache.get("key3") == "value3"

        # Wait for L1 to expire
        time.sleep(1.2)

        # L2 should still have it, so set_if_not_exists should fail
        assert cache.set_if_not_exists("key3", "value3_new", ttl=100) is False

        # Value should still be original from L2
        assert cache.get("key3") == "value3"

    def test_l2_ttl_with_zero_ttl_in_set(self):
        """Test that l2_ttl is used when ttl=0 is passed to set()."""
        l1 = InMemCache()
        l2 = InMemCache()

        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=2, l2_ttl=5)

        # Set with ttl=0 should use l1_ttl and l2_ttl defaults
        cache.set("key4", "value4", ttl=0)

        assert cache.get("key4") == "value4"

        # Wait for L1 to expire
        time.sleep(2.2)

        # L1 expired, but L2 should still have it (l2_ttl=5)
        assert l1.get("key4") is None
        assert l2.get("key4") == "value4"
        assert cache.get("key4") == "value4"


@pytest.mark.asyncio
@pytest.mark.usefixtures("cleanup")
class TestNoCachingWhenZero:
    """Ensure ttl/interval_seconds == 0 disables caching/background behavior."""

    async def test_ttlcache_ttl_zero_disables_caching(self):
        calls = {"n": 0}

        @TTLCache.cached("user:{}", ttl=0)
        async def get_user(user_id: int) -> int:
            calls["n"] += 1
            return calls["n"]

        # Each call should invoke the function (no caching)
        assert (await get_user(1)) == 1
        assert (await get_user(1)) == 2
        assert (await get_user(1)) == 3
        assert calls["n"] == 3

    async def test_swrcache_ttl_zero_disables_caching(self):
        calls = {"n": 0}

        @SWRCache.cached("data:{}", ttl=0, stale_ttl=10)
        async def get_data(key: str) -> int:
            calls["n"] += 1
            return calls["n"]

        # Each call should invoke the function (no SWR behavior)
        assert (await get_data("k")) == 1
        assert (await get_data("k")) == 2
        assert (await get_data("k")) == 3
        assert calls["n"] == 3

    async def test_bgcache_interval_zero_disables_background_and_cache(self):
        calls = {"n": 0}

        @BGCache.register_loader(key="no_bg", interval_seconds=0, ttl=None)
        async def load_data() -> int:
            calls["n"] += 1
            return calls["n"]

        # No background scheduler, no caching: each call increments
        assert (await load_data()) == 1
        assert (await load_data()) == 2
        assert (await load_data()) == 3
        assert calls["n"] == 3

    async def test_bgcache_ttl_zero_disables_background_and_cache(self):
        calls = {"n": 0}

        @BGCache.register_loader(key="no_bg_ttl", interval_seconds=10, ttl=0)
        async def load_data() -> int:
            calls["n"] += 1
            return calls["n"]

        # Because ttl == 0, wrapper should bypass cache and scheduler
        assert (await load_data()) == 1
        assert (await load_data()) == 2
