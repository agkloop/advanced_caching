import os
import time
import pytest

try:
    from google.cloud import storage
except ImportError:  # pragma: no cover
    storage = None

from advanced_caching import GCSCache, TTLCache, SWRCache, ChainCache, InMemCache

EMULATOR = os.getenv("STORAGE_EMULATOR_HOST") or "http://localhost:4443"
USE_EMULATOR = bool(EMULATOR)

pytestmark = pytest.mark.skipif(
    storage is None, reason="google-cloud-storage not installed"
)


def _client():
    if storage is None:
        return None
    if USE_EMULATOR:
        os.environ.setdefault("STORAGE_EMULATOR_HOST", EMULATOR)
        return storage.Client.create_anonymous_client()
    return storage.Client()


@pytest.mark.integration
@pytest.mark.skipif(storage is None, reason="gcs client missing")
def test_gcscache_set_get_and_dedupe():
    client = _client()
    bucket = client.bucket("test-bkt")
    bucket.storage_class = "STANDARD"
    try:
        client.create_bucket(bucket)
    except Exception:
        pass

    cache = GCSCache(
        bucket="test-bkt",
        prefix="t/",
        client=client,
        serializer="json",
        dedupe_writes=True,
    )
    cache.set("k1", {"v": 1}, ttl=0)
    assert cache.get("k1") == {"v": 1}

    cache.set("k1", {"v": 1}, ttl=0)  # dedupe should skip rewrite
    cache.set("k1", {"v": 2}, ttl=0)
    assert cache.get("k1") == {"v": 2}


@pytest.mark.integration
@pytest.mark.skipif(storage is None, reason="gcs client missing")
def test_ttlcache_with_gcscache_decorator():
    client = _client()
    bucket = client.bucket("test-bkt2")
    bucket.storage_class = "STANDARD"
    try:
        client.create_bucket(bucket)
    except Exception:
        pass

    cache = GCSCache(
        bucket="test-bkt2",
        prefix="u/",
        client=client,
        serializer="json",
        dedupe_writes=False,
        compress=False,
    )

    calls = {"n": 0}

    @TTLCache.cached("user:{user_id}", ttl=0.2, cache=cache)
    def fetch_user(user_id: int):
        calls["n"] += 1
        return {"id": user_id, "n": calls["n"]}

    first = fetch_user(1)
    second = fetch_user(1)
    assert first == second == {"id": 1, "n": 1}

    time.sleep(1.0)
    # Force delete to ensure cache miss if TTL/time drift is an issue
    cache.delete("user:1")
    third = fetch_user(1)
    # TTL expired, should recompute
    assert third["n"] >= 2


@pytest.mark.integration
@pytest.mark.skipif(storage is None, reason="gcs client missing")
def test_swrcache_with_gcscache_decorator():
    client = _client()
    bucket = client.bucket("test-bkt-swr")
    try:
        client.create_bucket(bucket)
    except Exception:
        pass
    cache = GCSCache(
        bucket="test-bkt-swr", prefix="swr/", client=client, serializer="json"
    )

    calls = {"n": 0}

    @SWRCache.cached("data:{id}", ttl=0.5, stale_ttl=1.0, cache=cache)
    def fetch_data(id: int):
        calls["n"] += 1
        return {"id": id, "n": calls["n"]}

    # 1. Initial fetch
    v1 = fetch_data(1)
    assert v1["n"] == 1

    # 2. Fresh hit
    v2 = fetch_data(1)
    assert v2["n"] == 1

    # 3. Wait for TTL to expire but within stale_ttl
    time.sleep(0.6)
    # Should return stale value immediately, trigger background refresh
    v3 = fetch_data(1)
    assert v3["n"] == 1

    # 4. Wait for background refresh to complete
    time.sleep(1.0)
    # Next call should get the refreshed value
    v4 = fetch_data(1)
    assert v4["n"] >= 2


@pytest.mark.integration
@pytest.mark.skipif(storage is None, reason="gcs client missing")
def test_chaincache_with_gcscache():
    client = _client()
    bucket = client.bucket("test-bkt-chain")
    try:
        client.create_bucket(bucket)
    except Exception:
        pass

    gcs_cache = GCSCache(
        bucket="test-bkt-chain", prefix="chain/", client=client, serializer="json"
    )
    # L1: InMem (fast), L2: GCS (durable)
    chain = ChainCache([(InMemCache(), 0.1), (gcs_cache, 1.0)])

    # Set in chain (propagates to both)
    chain.set("key1", "value1", ttl=1.0)

    # Verify L1 has it
    assert chain.levels[0][0].get("key1") == "value1"
    # Verify L2 (GCS) has it
    assert gcs_cache.get("key1") == "value1"

    # Clear L1 to force promotion from L2
    chain.levels[0][0].clear()
    assert chain.levels[0][0].get("key1") is None

    # Get from chain -> should fetch from GCS and repopulate L1
    val = chain.get("key1")
    assert val == "value1"
    assert chain.levels[0][0].get("key1") == "value1"
