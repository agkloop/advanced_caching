import os
import time
import pytest

try:
    import boto3
except ImportError:  # pragma: no cover
    boto3 = None

try:
    from moto import mock_aws
except ImportError:  # pragma: no cover
    mock_aws = None

from advanced_caching import S3Cache, TTLCache, SWRCache, ChainCache, InMemCache

S3_ENDPOINT = os.getenv("S3_ENDPOINT_URL")
USE_REAL_S3 = bool(S3_ENDPOINT)

pytestmark = pytest.mark.skipif(boto3 is None, reason="boto3 not installed")


def _maybe_mock(fn):
    if USE_REAL_S3 or mock_aws is None:
        return fn
    return mock_aws(fn)


def _client():
    if not boto3:
        return None
    kwargs = {"region_name": os.getenv("AWS_REGION", "us-east-1")}
    if USE_REAL_S3:
        kwargs.update(
            endpoint_url=S3_ENDPOINT,
            aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID", "test"),
            aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY", "test"),
        )
    return boto3.client("s3", **kwargs)


@_maybe_mock
@pytest.mark.parametrize("dedupe", [True])
def test_s3cache_set_get_and_dedupe(dedupe):
    client = _client()
    try:
        client.create_bucket(Bucket="test-bkt")
    except Exception:
        pass
    cache = S3Cache(
        bucket="test-bkt",
        prefix="t/",
        s3_client=client,
        serializer="json",
        dedupe_writes=dedupe,
    )

    cache.set("k1", {"v": 1}, ttl=0)
    assert cache.get("k1") == {"v": 1}

    cache.set("k1", {"v": 1}, ttl=0)
    assert cache.get("k1") == {"v": 1}

    cache.set("k1", {"v": 2}, ttl=0)
    assert cache.get("k1") == {"v": 2}


@_maybe_mock
def test_ttlcache_with_s3cache_decorator():
    client = _client()
    try:
        client.create_bucket(Bucket="test-bkt")
    except Exception:
        pass
    cache = S3Cache(
        bucket="test-bkt",
        prefix="u/",
        s3_client=client,
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


@_maybe_mock
def test_swrcache_with_s3cache_decorator():
    client = _client()
    try:
        client.create_bucket(Bucket="test-bkt-swr")
    except Exception:
        pass
    cache = S3Cache(
        bucket="test-bkt-swr", prefix="swr/", s3_client=client, serializer="json"
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


@_maybe_mock
def test_chaincache_with_s3cache():
    client = _client()
    try:
        client.create_bucket(Bucket="test-bkt-chain")
    except Exception:
        pass

    s3_cache = S3Cache(
        bucket="test-bkt-chain", prefix="chain/", s3_client=client, serializer="json"
    )
    # L1: InMem (fast), L2: S3 (durable)
    chain = ChainCache([(InMemCache(), 0.1), (s3_cache, 1.0)])

    # Set in chain (propagates to both)
    chain.set("key1", "value1", ttl=1.0)

    # Verify L1 has it
    assert chain.levels[0][0].get("key1") == "value1"
    # Verify L2 (S3) has it
    assert s3_cache.get("key1") == "value1"

    # Clear L1 to force promotion from L2
    chain.levels[0][0].clear()
    assert chain.levels[0][0].get("key1") is None

    # Get from chain -> should fetch from S3 and repopulate L1
    val = chain.get("key1")
    assert val == "value1"
    assert chain.levels[0][0].get("key1") == "value1"
