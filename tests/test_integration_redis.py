"""
Integration tests for Redis-backed caching.
Uses testcontainers-python to spin up a real Redis instance for testing.
"""

import pytest
import time

try:
    import redis
    from testcontainers.redis import RedisContainer

    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False

from advanced_caching import (
    CacheEntry,
    TTLCache,
    SWRCache,
    BGCache,
    RedisCache,
    HybridCache,
    InMemCache,
)


@pytest.fixture(scope="module")
def redis_container():
    """Fixture to start a Redis container for the entire test module."""
    if not HAS_REDIS:
        pytest.skip("testcontainers[redis] not installed")

    container = RedisContainer(image="redis:7-alpine")
    container.start()
    yield container
    container.stop()


@pytest.fixture
def redis_client(redis_container):
    """Fixture to create a Redis client connected to the container."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = redis.Redis(host=host, port=int(port))
    client.ping()
    client.flushdb()
    yield client
    client.flushdb()


class TestRedisCache:
    """Test RedisCache backend directly."""

    def test_redis_cache_basic_set_get(self, redis_client):
        """Test basic set and get operations on RedisCache."""
        cache = RedisCache(redis_client, prefix="test:")

        cache.set("key1", {"data": "value1"}, ttl=60)
        result = cache.get("key1")
        assert result == {"data": "value1"}

    def test_redis_cache_ttl_expiration(self, redis_client):
        """Test that Redis cache respects TTL."""
        cache = RedisCache(redis_client, prefix="test:")

        cache.set("expire_me", "value", ttl=1)
        assert cache.get("expire_me") == "value"

        time.sleep(1.1)
        assert cache.get("expire_me") is None

    def test_redis_cache_delete(self, redis_client):
        """Test delete operation on RedisCache."""
        cache = RedisCache(redis_client, prefix="test:")

        cache.set("key", "value", ttl=60)
        assert cache.exists("key")

        cache.delete("key")
        assert not cache.exists("key")

    def test_redis_cache_entry_roundtrip(self, redis_client):
        """Test get_entry/set_entry interoperability for RedisCache."""
        cache = RedisCache(redis_client, prefix="test:")

        entry = CacheEntry(
            value={"payload": True},
            fresh_until=time.time() + 5,
            created_at=time.time(),
        )

        cache.set_entry("entry_key", entry)

        loaded_entry = cache.get_entry("entry_key")
        assert isinstance(loaded_entry, CacheEntry)
        assert loaded_entry.value == {"payload": True}

        # Regular get should unwrap value
        assert cache.get("entry_key") == {"payload": True}

    def test_redis_cache_set_if_not_exists(self, redis_client):
        """Test atomic set_if_not_exists operation."""
        cache = RedisCache(redis_client, prefix="test:")

        result1 = cache.set_if_not_exists("atomic_key", "value1", ttl=60)
        assert result1 is True

        result2 = cache.set_if_not_exists("atomic_key", "value2", ttl=60)
        assert result2 is False

        assert cache.get("atomic_key") == "value1"

    def test_redis_cache_multiple_types(self, redis_client):
        """Test caching different data types in Redis."""
        cache = RedisCache(redis_client, prefix="test:")

        cache.set("str", "hello", ttl=60)
        assert cache.get("str") == "hello"

        data_dict = {"name": "test", "count": 42}
        cache.set("dict", data_dict, ttl=60)
        assert cache.get("dict") == data_dict

        data_list = [1, 2, 3, "four"]
        cache.set("list", data_list, ttl=60)
        assert cache.get("list") == data_list


class TestTTLCacheWithRedis:
    """Test TTLCache decorator with Redis backend."""

    def test_ttlcache_redis_basic(self, redis_client):
        """Test TTLCache with Redis backend."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="ttl:")

        @TTLCache.cached("user:{}", ttl=60, cache=cache)
        def get_user(user_id: int):
            calls["n"] += 1
            return {"id": user_id, "name": f"User{user_id}"}

        result1 = get_user(1)
        assert result1 == {"id": 1, "name": "User1"}
        assert calls["n"] == 1

        result2 = get_user(1)
        assert result2 == {"id": 1, "name": "User1"}
        assert calls["n"] == 1

        result3 = get_user(2)
        assert result3 == {"id": 2, "name": "User2"}
        assert calls["n"] == 2

    def test_ttlcache_redis_expiration(self, redis_client):
        """Test TTLCache with Redis respects TTL."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="ttl:")

        @TTLCache.cached("data:{}", ttl=1, cache=cache)
        def get_data(key: str):
            calls["n"] += 1
            return f"data_{key}"

        result1 = get_data("test")
        assert result1 == "data_test"
        assert calls["n"] == 1

        result2 = get_data("test")
        assert calls["n"] == 1

        time.sleep(1.1)

        result3 = get_data("test")
        assert result3 == "data_test"
        assert calls["n"] == 2

    def test_ttlcache_redis_named_template(self, redis_client):
        """Test TTLCache with Redis using named key template."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="ttl:")

        @TTLCache.cached("product:{product_id}", ttl=60, cache=cache)
        def get_product(*, product_id: int):
            calls["n"] += 1
            return {"id": product_id, "name": f"Product{product_id}"}

        result1 = get_product(product_id=100)
        assert result1 == {"id": 100, "name": "Product100"}
        assert calls["n"] == 1

        result2 = get_product(product_id=100)
        assert calls["n"] == 1


class TestSWRCacheWithRedis:
    """Test SWRCache with Redis backend."""

    def test_swrcache_redis_basic(self, redis_client):
        """Test SWRCache with Redis backend."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="swr:")

        @SWRCache.cached("product:{}", ttl=1, stale_ttl=1, cache=cache)
        def get_product(product_id: int):
            calls["n"] += 1
            return {"id": product_id, "count": calls["n"]}

        result1 = get_product(1)
        assert result1["count"] == 1
        assert calls["n"] == 1

        result2 = get_product(1)
        assert result2["count"] == 1
        assert calls["n"] == 1

    def test_swrcache_redis_stale_serve(self, redis_client):
        """Test SWRCache serves stale data while refreshing."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="swr:")

        @SWRCache.cached("data:{}", ttl=0.3, stale_ttl=0.5, cache=cache)
        def get_data(key: str):
            calls["n"] += 1
            return {"key": key, "count": calls["n"]}

        result1 = get_data("test")
        assert result1["count"] == 1

        time.sleep(0.4)

        result2 = get_data("test")
        assert result2["count"] == 1

        # Give background refresh enough time (Redis + thread scheduling)
        time.sleep(0.35)

        result3 = get_data("test")
        assert result3["count"] >= 2


class TestBGCacheWithRedis:
    """Test BGCache with Redis backend."""

    def test_bgcache_redis_sync_loader(self, redis_client):
        """Test BGCache with sync loader and Redis backend."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="bg:")

        @BGCache.register_loader(
            key="inventory",
            interval_seconds=10,
            run_immediately=True,
            cache=cache,
        )
        def load_inventory():
            calls["n"] += 1
            return {"items": [f"item_{i}" for i in range(3)]}

        time.sleep(0.1)

        result = load_inventory()
        assert result == {"items": ["item_0", "item_1", "item_2"]}
        assert calls["n"] == 1

        result2 = load_inventory()
        assert result2 == {"items": ["item_0", "item_1", "item_2"]}
        assert calls["n"] == 1

    def test_bgcache_redis_with_error_handler(self, redis_client):
        """Test BGCache error handling with Redis."""
        errors = []
        cache = RedisCache(redis_client, prefix="bg:")

        def on_error(exc):
            errors.append(exc)

        @BGCache.register_loader(
            key="failing_loader",
            interval_seconds=10,
            run_immediately=True,
            on_error=on_error,
            cache=cache,
        )
        def failing_loader():
            raise ValueError("Simulated failure")

        time.sleep(0.1)

        assert len(errors) == 1
        assert isinstance(errors[0], ValueError)


class TestHybridCacheWithRedis:
    """Test HybridCache (L1 memory + L2 Redis) backend."""

    def test_hybridcache_basic_flow(self, redis_client):
        """Test HybridCache with L1 (memory) and L2 (Redis)."""
        l2 = RedisCache(redis_client, prefix="hybrid:")
        cache = HybridCache(
            l1_cache=InMemCache(),
            l2_cache=l2,
            l1_ttl=1,
        )

        cache.set("key", {"data": "value"}, ttl=60)

        result1 = cache.get("key")
        assert result1 == {"data": "value"}

        assert cache.exists("key")

        cache.delete("key")
        assert not cache.exists("key")

    def test_hybridcache_l1_miss_l2_hit(self, redis_client):
        """Test HybridCache L1 miss, L2 hit, and L1 repopulation."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="hybrid:")
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=60)

        l2.set("key", "value_from_l2", 60)

        result = cache.get("key")
        assert result == "value_from_l2"

        assert l1.get("key") == "value_from_l2"

    def test_hybridcache_with_ttlcache(self, redis_client):
        """Test TTLCache using HybridCache backend."""
        l2 = RedisCache(redis_client, prefix="hybrid_ttl:")
        cache = HybridCache(
            l1_cache=InMemCache(),
            l2_cache=l2,
            l1_ttl=60,
        )

        calls = {"n": 0}

        @TTLCache.cached("user:{}", ttl=60, cache=cache)
        def get_user(user_id: int):
            calls["n"] += 1
            return {"id": user_id}

        result1 = get_user(1)
        assert result1 == {"id": 1}
        assert calls["n"] == 1

        result2 = get_user(1)
        assert result2 == {"id": 1}
        assert calls["n"] == 1


class TestRedisPerformance:
    """Performance tests with Redis backend."""

    def test_redis_cache_hit_performance(self, redis_client):
        """Verify Redis cache hits are fast."""
        cache = RedisCache(redis_client, prefix="perf:")

        cache.set("perf_key", {"data": "test"}, ttl=60)

        start = time.perf_counter()
        for _ in range(1000):
            result = cache.get("perf_key")
        duration = time.perf_counter() - start

        avg_time_ms = (duration / 1000) * 1000

        # Generous for CI environment
        assert avg_time_ms < 20, f"Redis cache hit too slow: {avg_time_ms:.3f}ms"
        assert result == {"data": "test"}

    def test_ttlcache_with_redis_performance(self, redis_client):
        """Test TTLCache performance with Redis backend."""
        cache = RedisCache(redis_client, prefix="perf_ttl:")

        @TTLCache.cached("item:{}", ttl=60, cache=cache)
        def get_item(item_id: int):
            return {"id": item_id}

        get_item(1)

        start = time.perf_counter()
        for _ in range(1000):
            get_item(1)
        duration = time.perf_counter() - start

        avg_time_ms = (duration / 1000) * 1000

        assert avg_time_ms < 25, f"TTLCache hit too slow: {avg_time_ms:.3f}ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
