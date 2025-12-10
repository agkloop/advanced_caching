"""
Fast and reliable unit tests for caching decorators.
Tests TTLCache, SWRCache, and BGCache functionality.
"""

import pytest
import time

from advanced_caching import BGCache, InMemCache, TTLCache, SWRCache


@pytest.fixture(autouse=True)
def cleanup():
    """Clean up scheduler between tests."""
    yield
    try:
        BGCache.shutdown(wait=False)
    except:
        pass
    time.sleep(0.05)


class TestTTLCache:
    """TTLCache decorator tests."""

    def test_basic_caching(self):
        """Test basic TTL caching with function calls."""
        call_count = {"count": 0}

        @TTLCache.cached("user:{}", ttl=60)
        def get_user(user_id):
            call_count["count"] += 1
            return {"id": user_id, "name": f"User{user_id}"}

        # First call - cache miss
        result1 = get_user(1)
        assert result1 == {"id": 1, "name": "User1"}
        assert call_count["count"] == 1

        # Second call - cache hit
        result2 = get_user(1)
        assert result2 == {"id": 1, "name": "User1"}
        assert call_count["count"] == 1  # Not incremented

        # Different key - cache miss
        result3 = get_user(2)
        assert result3 == {"id": 2, "name": "User2"}
        assert call_count["count"] == 2

    def test_ttl_expiration(self):
        """Test that cache expires after TTL."""
        call_count = {"count": 0}

        @TTLCache.cached("data:{}", ttl=0.5)
        def get_data(key):
            call_count["count"] += 1
            return {"key": key, "count": call_count["count"]}

        # First call
        result1 = get_data("test")
        assert result1["count"] == 1
        assert call_count["count"] == 1

        # Cache should still be valid
        result2 = get_data("test")
        assert result2["count"] == 1
        assert call_count["count"] == 1

        # Wait for expiration
        time.sleep(0.6)

        # Cache should be expired, function called again
        result3 = get_data("test")
        assert result3["count"] == 2
        assert call_count["count"] == 2

    def test_custom_cache_backend(self):
        """Test TTLCache with custom backend."""
        custom_cache = InMemCache()

        @TTLCache.cached("item:{}", ttl=60, cache=custom_cache)
        def get_item(item_id):
            return {"id": item_id}

        result = get_item(123)
        assert result == {"id": 123}

        # Verify in custom cache
        assert custom_cache.exists("item:123")

    def test_callable_key_function(self):
        """Test TTLCache with callable key function."""

        @TTLCache.cached(key=lambda user_id: f"user:{user_id}", ttl=60)
        def get_user(user_id):
            return {"id": user_id}

        result = get_user(42)
        assert result == {"id": 42}

    def test_isolated_caches(self):
        """Test that each TTL cached function has its own cache."""

        @TTLCache.cached("user:{}", ttl=60)
        def get_user(user_id):
            return {"type": "user", "id": user_id}

        @TTLCache.cached("product:{}", ttl=60)
        def get_product(product_id):
            return {"type": "product", "id": product_id}

        # Each should have its own cache
        assert get_user._cache is not get_product._cache

        # Both should work
        assert get_user(1)["type"] == "user"
        assert get_product(1)["type"] == "product"


class TestSWRCache:
    """SWRCache (Stale-While-Revalidate) tests."""

    def test_fresh_cache_hit(self):
        """Test SWR with fresh cache returns immediately."""
        call_count = {"count": 0}

        @SWRCache.cached("user:{}", ttl=60, stale_ttl=30)
        def get_user(user_id):
            call_count["count"] += 1
            return {"id": user_id, "count": call_count["count"]}

        # First call - cache miss
        result1 = get_user(1)
        assert result1["count"] == 1
        assert call_count["count"] == 1

        # Second call - should hit fresh cache
        result2 = get_user(1)
        assert result2["count"] == 1  # Same cached value
        assert call_count["count"] == 1  # Function not called again

    def test_stale_with_background_refresh(self):
        """Test SWR serves stale data while refreshing in background."""
        call_count = {"count": 0}

        @SWRCache.cached("data:{}", ttl=0.3, stale_ttl=0.5)
        def get_data(key):
            call_count["count"] += 1
            return {"key": key, "count": call_count["count"]}

        # First call
        result1 = get_data("test")
        assert result1["count"] == 1
        assert call_count["count"] == 1

        # Wait for data to become stale but within grace period
        time.sleep(0.4)

        # Should return stale value and refresh in background
        result2 = get_data("test")
        assert result2["count"] == 1  # Still getting stale data
        # Background refresh may or may not have completed yet

        # Wait for background refresh to complete
        time.sleep(0.2)

        # Now should have fresh data
        result3 = get_data("test")
        assert result3["count"] >= 2  # Should be refreshed

    def test_too_stale_refetch(self):
        """Test SWR refetches when too stale."""
        call_count = {"count": 0}

        @SWRCache.cached("data:{}", ttl=0.2, stale_ttl=0.2)
        def get_data(key):
            call_count["count"] += 1
            return {"key": key, "count": call_count["count"]}

        # First call
        result1 = get_data("test")
        assert result1["count"] == 1

        # Wait until beyond TTL + stale_ttl
        time.sleep(0.5)

        # Should refetch immediately (not within grace period)
        result2 = get_data("test")
        assert result2["count"] == 2  # Refetched
        assert call_count["count"] == 2

    def test_custom_cache_backend(self):
        """Test SWRCache with custom backend."""
        custom_cache = InMemCache()

        @SWRCache.cached("item:{}", ttl=60, stale_ttl=30, cache=custom_cache)
        def get_item(item_id):
            return {"id": item_id}

        result = get_item(123)
        assert result == {"id": 123}


class TestBGCache:
    """BGCache (Background Scheduler) tests."""

    def test_sync_loader_immediate(self):
        """Test sync loader with immediate execution."""
        call_count = {"count": 0}

        @BGCache.register_loader("sync_test", interval_seconds=10, run_immediately=True)
        def load_data():
            call_count["count"] += 1
            return {"value": call_count["count"]}

        time.sleep(0.1)  # Wait for initial load

        # First call should return cached data
        result = load_data()
        assert result == {"value": 1}
        assert call_count["count"] == 1

        # Second call should still use cache
        result2 = load_data()
        assert result2 == {"value": 1}
        assert call_count["count"] == 1  # Not called again

    def test_sync_loader_no_immediate(self):
        """Test sync loader without immediate execution."""
        call_count = {"count": 0}

        @BGCache.register_loader(
            "no_immediate", interval_seconds=10, run_immediately=False
        )
        def load_data():
            call_count["count"] += 1
            return {"value": call_count["count"]}

        time.sleep(0.1)

        # Should not have been called yet
        assert call_count["count"] == 0

        # First call will execute the function since cache is empty
        result = load_data()
        assert result == {"value": 1}
        assert call_count["count"] == 1

    def test_custom_cache_backend(self):
        """Test BGCache using custom cache backend."""
        custom_cache = InMemCache()

        @BGCache.register_loader(
            "custom", interval_seconds=10, run_immediately=True, cache=custom_cache
        )
        def load_data():
            return {"custom": True}

        time.sleep(0.1)

        # Verify data is in custom cache
        cached_value = custom_cache.get("custom")
        assert cached_value == {"custom": True}

        # Call function
        result = load_data()
        assert result == {"custom": True}

    def test_isolated_cache_instances(self):
        """Test that each loader has its own cache."""

        @BGCache.register_loader("loader1", interval_seconds=10, run_immediately=True)
        def load1():
            return {"id": 1}

        @BGCache.register_loader("loader2", interval_seconds=10, run_immediately=True)
        def load2():
            return {"id": 2}

        time.sleep(0.1)

        # Each should have its own cache
        assert load1._cache is not load2._cache
        assert load1._cache_key == "loader1"
        assert load2._cache_key == "loader2"

        # Each should have correct data
        assert load1() == {"id": 1}
        assert load2() == {"id": 2}

    def test_error_handling(self):
        """Test error handler is called on failure."""
        errors = []

        def error_handler(e):
            errors.append(e)

        @BGCache.register_loader(
            "error_test",
            interval_seconds=10,
            run_immediately=True,
            on_error=error_handler,
        )
        def load_data():
            raise ValueError("Test error")

        time.sleep(0.1)

        # Error should have been captured
        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)
        assert str(errors[0]) == "Test error"

    def test_periodic_refresh(self):
        """Test that data refreshes periodically."""
        call_count = {"count": 0}

        @BGCache.register_loader("periodic", interval_seconds=0.5, run_immediately=True)
        def load_data():
            call_count["count"] += 1
            return {"value": call_count["count"]}

        # Wait for initial load
        time.sleep(0.1)
        assert call_count["count"] == 1

        # Wait for one refresh
        time.sleep(0.6)
        assert call_count["count"] >= 2

        # Get updated data
        result = load_data()
        assert result["value"] >= 2

    def test_multiple_loaders(self):
        """Test multiple loaders can coexist."""

        @BGCache.register_loader("loader_a", interval_seconds=10, run_immediately=True)
        def load_a():
            return {"name": "a"}

        @BGCache.register_loader("loader_b", interval_seconds=10, run_immediately=True)
        def load_b():
            return {"name": "b"}

        @BGCache.register_loader("loader_c", interval_seconds=10, run_immediately=True)
        def load_c():
            return {"name": "c"}

        time.sleep(0.15)

        # All should work independently
        assert load_a()["name"] == "a"
        assert load_b()["name"] == "b"
        assert load_c()["name"] == "c"


class TestCachePerformance:
    """Performance and speed tests."""

    def test_cache_hit_speed(self):
        """Test that cache hits are fast."""

        @BGCache.register_loader("perf_test", interval_seconds=10, run_immediately=True)
        def load_data():
            time.sleep(0.01)  # Simulate slow operation
            return {"data": "value"}

        time.sleep(0.05)  # Wait for initial load

        # Measure cache hit time
        start = time.perf_counter()
        for _ in range(1000):
            result = load_data()
        duration = time.perf_counter() - start

        # Should be very fast (<1ms per call on average)
        avg_time = duration / 1000
        assert avg_time < 0.001, f"Cache hit too slow: {avg_time * 1000:.3f}ms"
        assert result == {"data": "value"}

    def test_ttl_cache_hit_speed(self):
        """Test TTLCache hit speed."""

        @TTLCache.cached("item:{}", ttl=60)
        def get_item(item_id):
            time.sleep(0.001)  # Simulate work
            return {"id": item_id}

        # Prime cache
        get_item(1)

        # Measure cache hits
        start = time.perf_counter()
        for _ in range(1000):
            get_item(1)
        duration = time.perf_counter() - start

        avg_time = duration / 1000
        assert avg_time < 0.0005, f"TTL cache hit too slow: {avg_time * 1000:.3f}ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
