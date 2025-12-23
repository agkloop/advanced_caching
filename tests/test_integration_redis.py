"""
Integration tests for Redis-backed caching.
Uses testcontainers-python to spin up a real Redis instance for testing.
"""

import pickle
import pytest
import time
import asyncio
from typing import Any

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
    JsonSerializer,
)


@pytest.fixture(autouse=True)
def reset_scheduler():
    yield
    BGCache.shutdown(wait=False)


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

    def test_redis_cache_json_serializer(self, redis_client):
        """Ensure JSON serializer roundtrips values and entries."""
        cache = RedisCache(redis_client, prefix="json:", serializer=JsonSerializer())

        payload = {"a": 1, "b": [1, 2, 3]}
        cache.set("payload", payload, ttl=60)
        assert cache.get("payload") == payload

        entry = CacheEntry(
            value={"ok": True},
            fresh_until=time.time() + 5,
            created_at=time.time(),
        )
        cache.set_entry("entry", entry, ttl=5)
        loaded = cache.get_entry("entry")
        assert isinstance(loaded, CacheEntry)
        assert loaded.value == entry.value

    def test_redis_cache_custom_serializer_handles_entries(self, redis_client):
        """Custom serializer can opt-out of wrapping CacheEntry."""

        class PickleSerializer:
            handles_entries = True

            @staticmethod
            def dumps(obj: Any) -> bytes:
                return pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)

            @staticmethod
            def loads(data: bytes) -> Any:
                return pickle.loads(data)

        cache = RedisCache(
            redis_client, prefix="custom:", serializer=PickleSerializer()
        )

        cache.set("k", {"v": 1}, ttl=30)
        assert cache.get("k") == {"v": 1}

        entry = CacheEntry(
            value={"v": 2},
            fresh_until=time.time() + 5,
            created_at=time.time(),
        )
        cache.set_entry("entry", entry, ttl=5)
        loaded = cache.get_entry("entry")
        assert isinstance(loaded, CacheEntry)
        assert loaded.value == entry.value


@pytest.mark.asyncio
class TestTTLCacheWithRedis:
    """Test TTLCache decorator with Redis backend."""

    async def test_ttlcache_redis_basic(self, redis_client):
        """Test TTLCache with Redis backend."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="ttl:")

        @TTLCache.cached("user:{}", ttl=60, cache=cache)
        async def get_user(user_id: int):
            calls["n"] += 1
            return {"id": user_id, "name": f"User{user_id}"}

        result1 = await get_user(1)
        assert result1 == {"id": 1, "name": "User1"}
        assert calls["n"] == 1

        result2 = await get_user(1)
        assert result2 == {"id": 1, "name": "User1"}
        assert calls["n"] == 1

        result3 = await get_user(2)
        assert result3 == {"id": 2, "name": "User2"}
        assert calls["n"] == 2

    async def test_ttlcache_redis_expiration(self, redis_client):
        """Test TTLCache with Redis respects TTL."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="ttl:")

        @TTLCache.cached("data:{}", ttl=1, cache=cache)
        async def get_data(key: str):
            calls["n"] += 1
            return f"data_{key}"

        result1 = await get_data("test")
        assert result1 == "data_test"
        assert calls["n"] == 1

        result2 = await get_data("test")
        assert calls["n"] == 1

        await asyncio.sleep(1.1)

        result3 = await get_data("test")
        assert result3 == "data_test"
        assert calls["n"] == 2

    async def test_ttlcache_redis_named_template(self, redis_client):
        """Test TTLCache with Redis using named key template."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="ttl:")

        @TTLCache.cached("product:{product_id}", ttl=60, cache=cache)
        async def get_product(*, product_id: int):
            calls["n"] += 1
            return {"id": product_id, "name": f"Product{product_id}"}

        result1 = await get_product(product_id=100)
        assert result1 == {"id": 100, "name": "Product100"}
        assert calls["n"] == 1

        await get_product(product_id=100)
        assert calls["n"] == 1


@pytest.mark.asyncio
class TestSWRCacheWithRedis:
    """Test SWRCache with Redis backend."""

    async def test_swrcache_redis_basic(self, redis_client):
        """Test SWRCache with Redis backend."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="swr:")

        @SWRCache.cached("product:{}", ttl=1, stale_ttl=1, cache=cache)
        async def get_product(product_id: int):
            calls["n"] += 1
            return {"id": product_id, "count": calls["n"]}

        result1 = await get_product(1)
        assert result1["count"] == 1
        assert calls["n"] == 1

        result2 = await get_product(1)
        assert result2["count"] == 1
        assert calls["n"] == 1

    async def test_swrcache_redis_stale_serve(self, redis_client):
        """Test SWRCache serves stale data while refreshing."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="swr:")

        @SWRCache.cached("data:{}", ttl=0.3, stale_ttl=0.5, cache=cache)
        async def get_data(key: str):
            calls["n"] += 1
            return {"key": key, "count": calls["n"]}

        result1 = await get_data("test")
        assert result1["count"] == 1

        await asyncio.sleep(0.4)

        result2 = await get_data("test")
        assert result2["count"] == 1

        # Give background refresh enough time (Redis + thread scheduling)
        await asyncio.sleep(0.35)

        result3 = await get_data("test")
        assert result3["count"] >= 2


@pytest.mark.asyncio
class TestBGCacheWithRedis:
    """Test BGCache with Redis backend."""

    async def test_bgcache_redis_sync_loader(self, redis_client):
        """Test BGCache with sync loader and Redis backend."""
        calls = {"n": 0}
        cache = RedisCache(redis_client, prefix="bg:")

        @BGCache.register_loader(
            key="inventory",
            interval_seconds=10,
            run_immediately=True,
            cache=cache,
        )
        async def load_inventory():
            calls["n"] += 1
            return {"items": [f"item_{i}" for i in range(3)]}

        await asyncio.sleep(0.1)

        result = await load_inventory()
        assert result == {"items": ["item_0", "item_1", "item_2"]}
        assert calls["n"] == 1

        result2 = await load_inventory()
        assert result2 == {"items": ["item_0", "item_1", "item_2"]}
        assert calls["n"] == 1

    async def test_bgcache_redis_with_error_handler(self, redis_client):
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
        async def failing_loader():
            raise ValueError("Simulated failure")

        await asyncio.sleep(0.1)

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

    @pytest.mark.asyncio
    async def test_hybridcache_with_ttlcache(self, redis_client):
        """Test TTLCache using HybridCache backend."""
        l2 = RedisCache(redis_client, prefix="hybrid_ttl:")
        cache = HybridCache(
            l1_cache=InMemCache(),
            l2_cache=l2,
            l1_ttl=60,
        )

        calls = {"n": 0}

        @TTLCache.cached("user:{}", ttl=60, cache=cache)
        async def get_user(user_id: int):
            calls["n"] += 1
            return {"id": user_id}

        result1 = await get_user(1)
        assert result1 == {"id": 1}
        assert calls["n"] == 1

        result2 = await get_user(1)
        assert result2 == {"id": 1}
        assert calls["n"] == 1

    def test_hybridcache_l2_ttl_defaults_to_double_l1(self, redis_client):
        """Test l2_ttl defaults to l1_ttl * 2."""
        l2 = RedisCache(redis_client, prefix="hybrid_l2:")
        cache = HybridCache(l1_cache=InMemCache(), l2_cache=l2, l1_ttl=30)

        assert cache.l1_ttl == 30
        assert cache.l2_ttl == 60

    def test_hybridcache_l2_ttl_explicit_value(self, redis_client):
        """Test explicit l2_ttl is respected."""
        l2 = RedisCache(redis_client, prefix="hybrid_l2:")
        cache = HybridCache(l1_cache=InMemCache(), l2_cache=l2, l1_ttl=10, l2_ttl=100)

        assert cache.l1_ttl == 10
        assert cache.l2_ttl == 100

    def test_hybridcache_l2_ttl_persistence(self, redis_client):
        """Test that l2_ttl allows L2 to persist longer than L1."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="hybrid_persist:")
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=1, l2_ttl=10)

        cache.set("key1", "value1", ttl=100)

        # Both L1 and L2 should have it initially
        assert l1.get("key1") == "value1"
        assert l2.get("key1") == "value1"

        # Wait for L1 to expire
        time.sleep(1.2)

        # L1 expired, L2 should still have it
        assert l1.get("key1") is None
        assert l2.get("key1") == "value1"

        # HybridCache should fetch from L2 and repopulate L1
        assert cache.get("key1") == "value1"
        assert l1.get("key1") == "value1"

    def test_hybridcache_l2_ttl_with_set_entry(self, redis_client):
        """Test set_entry respects l2_ttl."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="hybrid_entry:")
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=1, l2_ttl=10)

        now = time.time()
        entry = CacheEntry(value="test_value", fresh_until=now + 100, created_at=now)

        cache.set_entry("key2", entry, ttl=100)

        # Both should have it
        assert cache.get("key2") == "test_value"
        assert l1.get("key2") == "test_value"
        assert l2.get("key2") == "test_value"

        # Wait for L1 to expire
        time.sleep(1.2)

        # L1 expired, L2 still has it
        assert l1.get("key2") is None
        assert l2.get("key2") == "test_value"

    def test_hybridcache_l2_ttl_with_set_if_not_exists(self, redis_client):
        """Test set_if_not_exists respects l2_ttl."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="hybrid_atomic:")
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=1, l2_ttl=10)

        # First set succeeds
        assert cache.set_if_not_exists("key3", "value3", ttl=100) is True

        # Wait for L1 to expire
        time.sleep(1.2)

        # L2 should still have it, so second set fails
        assert cache.set_if_not_exists("key3", "new_value", ttl=100) is False

        # Value should be from L2
        assert cache.get("key3") == "value3"

    def test_hybridcache_l2_ttl_shorter_than_requested(self, redis_client):
        """Test that l2_ttl caps the TTL when set() is called with larger TTL."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="hybrid_cap:")
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=2, l2_ttl=3)

        # Set with large TTL, should be capped by l2_ttl
        cache.set("key4", "value4", ttl=1000)

        # Check that Redis has the key with capped TTL
        redis_ttl = redis_client.ttl("hybrid_cap:key4")
        # TTL should be approximately l2_ttl (3 seconds), allow some margin
        assert 2 <= redis_ttl <= 4

    @pytest.mark.asyncio
    async def test_hybridcache_with_bgcache_and_l2_ttl(self, redis_client):
        """Test BGCache with HybridCache using l2_ttl."""
        l2 = RedisCache(redis_client, prefix="hybrid_bg:")
        cache = HybridCache(l1_cache=InMemCache(), l2_cache=l2, l1_ttl=10, l2_ttl=60)

        calls = {"n": 0}

        @BGCache.register_loader(
            key="config_with_l2",
            interval_seconds=30,
            run_immediately=True,
            cache=cache,
        )
        async def load_config():
            calls["n"] += 1
            return {"setting": "value", "count": calls["n"]}

        await asyncio.sleep(0.1)

        result = await load_config()
        assert result["count"] == 1
        assert calls["n"] == 1

        # Verify it's cached
        result2 = await load_config()
        assert result2["count"] == 1
        assert calls["n"] == 1


class TestHybridCacheEdgeCases:
    """Edge case tests for HybridCache with Redis."""

    def test_hybridcache_zero_l1_ttl(self, redis_client):
        """Test HybridCache with zero L1 TTL (infinite TTL in L1)."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="edge_zero:")
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=0, l2_ttl=10)

        cache.set("key", "value", ttl=10)

        # With l1_ttl=0, L1 stores with infinite TTL
        assert l1.get("key") == "value"
        # L2 should have it with l2_ttl cap
        assert l2.get("key") == "value"
        # HybridCache should return it
        assert cache.get("key") == "value"

        # Wait for L2 to expire
        time.sleep(10.2)

        # L1 still has it (infinite), L2 expired
        assert l1.get("key") == "value"
        assert l2.get("key") is None

    def test_hybridcache_large_values(self, redis_client):
        """Test HybridCache with large values."""
        l2 = RedisCache(redis_client, prefix="edge_large:")
        cache = HybridCache(l1_cache=InMemCache(), l2_cache=l2, l1_ttl=60)

        # Large nested structure
        large_value = {
            f"key_{i}": {"data": [j for j in range(100)]} for i in range(100)
        }

        cache.set("large", large_value, ttl=60)
        result = cache.get("large")
        assert result == large_value

    def test_hybridcache_special_characters_in_keys(self, redis_client):
        """Test HybridCache with special characters in keys."""
        l2 = RedisCache(redis_client, prefix="edge_special:")
        cache = HybridCache(l1_cache=InMemCache(), l2_cache=l2, l1_ttl=60)

        special_keys = [
            "user:123",
            "email:test@example.com",
            "path:/api/v1/users",
            "query:name=test&id=1",
        ]

        for key in special_keys:
            cache.set(key, f"value_for_{key}", ttl=60)
            assert cache.get(key) == f"value_for_{key}"

    def test_hybridcache_concurrent_access(self, redis_client):
        """Test HybridCache under concurrent access."""
        import concurrent.futures

        l2 = RedisCache(redis_client, prefix="edge_concurrent:")
        cache = HybridCache(l1_cache=InMemCache(), l2_cache=l2, l1_ttl=60)

        def write_and_read(i):
            key = f"concurrent_{i}"
            cache.set(key, f"value_{i}", ttl=60)
            result = cache.get(key)
            return result == f"value_{i}"

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            results = list(executor.map(write_and_read, range(100)))

        assert all(results)

    def test_hybridcache_delete_propagates_to_both_layers(self, redis_client):
        """Test that delete removes from both L1 and L2."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="edge_delete:")
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=60)

        cache.set("delete_me", "value", ttl=60)

        # Verify both have it
        assert l1.get("delete_me") == "value"
        assert l2.get("delete_me") == "value"

        # Delete
        cache.delete("delete_me")

        # Verify both don't have it
        assert l1.get("delete_me") is None
        assert l2.get("delete_me") is None
        assert cache.get("delete_me") is None

    def test_hybridcache_none_values(self, redis_client):
        """Test HybridCache correctly handles None values."""
        l2 = RedisCache(redis_client, prefix="edge_none:")
        cache = HybridCache(l1_cache=InMemCache(), l2_cache=l2, l1_ttl=60)

        # Set explicit None value
        cache.set("none_key", None, ttl=60)
        result = cache.get("none_key")
        assert result is None

        # But key should exist
        assert cache.exists("none_key")

    def test_hybridcache_json_serializer_with_l2_ttl(self, redis_client):
        """Test HybridCache with JSON serializer and l2_ttl."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="edge_json:", serializer=JsonSerializer())
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=1, l2_ttl=10)

        data = {"string": "test", "number": 42, "list": [1, 2, 3]}
        cache.set("json_key", data, ttl=100)

        assert cache.get("json_key") == data

        # Wait for L1 to expire
        time.sleep(1.2)

        # Should still get from L2
        assert cache.get("json_key") == data

    def test_hybridcache_very_short_l2_ttl(self, redis_client):
        """Test HybridCache with very short l2_ttl caps L2 expiration."""
        l1 = InMemCache()
        l2 = RedisCache(redis_client, prefix="edge_short:")
        # L1 has longer TTL (60s) but L2 expires quickly (1s)
        cache = HybridCache(l1_cache=l1, l2_cache=l2, l1_ttl=60, l2_ttl=1)

        cache.set("short_ttl", "value", ttl=100)

        # Initially both should have it
        assert cache.get("short_ttl") == "value"

        # Wait for L2 to expire (l2_ttl=1 caps the L2 TTL)
        time.sleep(1.2)

        # L1 should still have it (l1_ttl=60), but L2 expired
        assert l1.get("short_ttl") == "value"
        assert l2.get("short_ttl") is None
        # HybridCache should return from L1
        assert cache.get("short_ttl") == "value"


@pytest.mark.asyncio
class TestCacheRehydration:
    """Test that decorators can retrieve existing data from Redis without re-executing functions."""

    async def test_ttlcache_rehydrates_from_redis(self, redis_client):
        """Test TTLCache retrieves existing Redis data without executing function."""
        # Pre-populate Redis
        test_data = {"result": "from_redis"}
        redis_client.setex("compute:42", 60, pickle.dumps(test_data))

        call_count = 0

        @TTLCache.cached(
            "compute:{}",
            ttl=60,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(),
                l2_cache=RedisCache(redis_client=redis_client),
                l1_ttl=60,
            ),
        )
        async def compute(x):
            nonlocal call_count
            call_count += 1
            return {"result": f"computed_{x}"}

        # First call should retrieve from Redis without executing function
        result = await compute(42)
        assert result == test_data
        assert call_count == 0, "Function should not execute when data exists in Redis"

        # Second call should hit L1 cache
        result = await compute(42)
        assert result == test_data
        assert call_count == 0

    async def test_swrcache_rehydrates_from_redis(self, redis_client):
        """Test SWRCache retrieves existing Redis data without executing function."""
        # Pre-populate Redis with CacheEntry
        now = time.time()
        entry = CacheEntry(
            value={"result": "from_redis"}, fresh_until=now + 60, created_at=now
        )
        redis_cache = RedisCache(redis_client=redis_client)
        redis_cache.set_entry("fetch:99", entry, ttl=60)

        call_count = 0

        @SWRCache.cached(
            "fetch:{}",
            ttl=60,
            stale_ttl=30,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(),
                l2_cache=RedisCache(redis_client=redis_client),
                l1_ttl=60,
            ),
        )
        async def fetch(x):
            nonlocal call_count
            call_count += 1
            return {"result": f"fetched_{x}"}

        # First call should retrieve from Redis without executing function
        result = await fetch(99)
        assert result == {"result": "from_redis"}
        assert call_count == 0, "Function should not execute when data exists in Redis"

        # Second call should hit L1 cache
        result = await fetch(99)
        assert result == {"result": "from_redis"}
        assert call_count == 0

    async def test_bgcache_rehydrates_from_redis(self, redis_client):
        """Test BGCache retrieves existing Redis data without executing function on init."""
        # Pre-populate Redis
        test_data = {"users": ["Alice", "Bob", "Charlie"]}
        redis_client.setex("users_list_rehydrate", 60, pickle.dumps(test_data))

        call_count = 0

        @BGCache.register_loader(
            key="users_list_rehydrate",
            interval_seconds=60,
            run_immediately=True,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(),
                l2_cache=RedisCache(redis_client=redis_client),
                l1_ttl=60,
            ),
        )
        async def load_users():
            nonlocal call_count
            call_count += 1
            return {"users": ["New1", "New2"]}

        # Function should not execute during init (data exists in Redis)
        assert call_count == 0, "Function should not execute when data exists in Redis"

        # First call should hit L1 cache
        result = await load_users()
        assert result == test_data
        assert call_count == 0

        BGCache.shutdown(wait=False)

    async def test_ttlcache_executes_on_cache_miss(self, redis_client):
        """Test TTLCache executes function when Redis is empty."""
        redis_client.flushdb()

        call_count = 0

        @TTLCache.cached(
            "compute:{}",
            ttl=60,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(),
                l2_cache=RedisCache(redis_client=redis_client),
                l1_ttl=60,
            ),
        )
        async def compute(x):
            nonlocal call_count
            call_count += 1
            return {"result": f"computed_{x}"}

        # First call should execute function (cache miss)
        result = await compute(42)
        assert result == {"result": "computed_42"}
        assert call_count == 1

        # Second call should hit L1 cache
        result = await compute(42)
        assert result == {"result": "computed_42"}
        assert call_count == 1

    async def test_swrcache_executes_on_cache_miss(self, redis_client):
        """Test SWRCache executes function when Redis is empty."""
        redis_client.flushdb()

        call_count = 0

        @SWRCache.cached(
            "fetch:{}",
            ttl=60,
            stale_ttl=30,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(),
                l2_cache=RedisCache(redis_client=redis_client),
                l1_ttl=60,
            ),
        )
        async def fetch(x):
            nonlocal call_count
            call_count += 1
            return {"result": f"fetched_{x}"}

        # First call should execute function (cache miss)
        result = await fetch(99)
        assert result == {"result": "fetched_99"}
        assert call_count == 1

        # Second call should hit L1 cache
        result = await fetch(99)
        assert result == {"result": "fetched_99"}
        assert call_count == 1

    async def test_bgcache_executes_on_cache_miss(self, redis_client):
        """Test BGCache executes function on init when Redis is empty."""
        redis_client.flushdb()

        call_count = 0

        @BGCache.register_loader(
            key="empty_test_bgcache",
            interval_seconds=60,
            run_immediately=True,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(),
                l2_cache=RedisCache(redis_client=redis_client),
                l1_ttl=60,
            ),
        )
        async def load_data():
            nonlocal call_count
            call_count += 1
            return {"data": "fresh_load"}

        # Function should execute during init (cache miss)
        await asyncio.sleep(0.1)
        assert call_count == 1

        # First call should hit L1 cache
        result = await load_data()
        assert result == {"data": "fresh_load"}
        assert call_count == 1

        BGCache.shutdown(wait=False)

    async def test_ttlcache_different_args_separate_entries(self, redis_client):
        """Test TTLCache creates separate cache entries for different arguments."""
        # Pre-populate Redis with data for arg=10
        test_data = {"result": "from_redis_10"}
        redis_client.setex("compute:10", 60, pickle.dumps(test_data))

        call_count = 0

        @TTLCache.cached(
            "compute:{}",
            ttl=60,
            cache=lambda: HybridCache(
                l1_cache=InMemCache(),
                l2_cache=RedisCache(redis_client=redis_client),
                l1_ttl=60,
            ),
        )
        async def compute(x):
            nonlocal call_count
            call_count += 1
            return {"result": f"computed_{x}"}

        # Call with arg=10 should get from Redis
        result = await compute(10)
        assert result == test_data
        assert call_count == 0

        # Call with arg=20 should execute function (no Redis data)
        result = await compute(20)
        assert result == {"result": "computed_20"}
        assert call_count == 1

        # Call with arg=10 again should get from L1
        result = await compute(10)
        assert result == test_data
        assert call_count == 1


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

    @pytest.mark.asyncio
    async def test_ttlcache_with_redis_performance(self, redis_client):
        """Test TTLCache performance with Redis backend."""
        cache = RedisCache(redis_client, prefix="perf_ttl:")

        @TTLCache.cached("item:{}", ttl=60, cache=cache)
        async def get_item(item_id: int):
            return {"id": item_id}

        await get_item(1)

        start = time.perf_counter()
        for _ in range(1000):
            await get_item(1)
        duration = time.perf_counter() - start

        avg_time_ms = (duration / 1000) * 1000

        assert avg_time_ms < 25, f"TTLCache hit too slow: {avg_time_ms:.3f}ms"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
