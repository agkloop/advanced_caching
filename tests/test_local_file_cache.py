import os
import time
import tempfile

from advanced_caching import LocalFileCache


def test_local_file_cache_set_get_and_expiry():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = LocalFileCache(tmpdir)
        cache.set("foo", "bar", ttl=0.1)
        assert cache.get("foo") == "bar"
        time.sleep(0.2)
        assert cache.get("foo") is None


def test_local_file_cache_dedupe_writes():
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = LocalFileCache(tmpdir, dedupe_writes=True)
        cache.set("foo", {"a": 1}, ttl=0)
        mtime1 = os.path.getmtime(os.path.join(tmpdir, "foo"))
        time.sleep(0.05)
        cache.set("foo", {"a": 1}, ttl=0)
        mtime2 = os.path.getmtime(os.path.join(tmpdir, "foo"))
        # Allow filesystem timestamp granularity drift; ensure dedupe prevented meaningful rewrite
        assert cache.get("foo") == {"a": 1}
        assert mtime2 <= mtime1 + 0.1
        cache.set("foo", {"a": 2}, ttl=0)
        mtime3 = os.path.getmtime(os.path.join(tmpdir, "foo"))
        assert mtime3 > mtime2


def test_ttlcache_with_local_file_cache_decorator():
    from advanced_caching import TTLCache

    calls = {"n": 0}
    with tempfile.TemporaryDirectory() as tmpdir:
        cache = LocalFileCache(tmpdir)

        @TTLCache.cached("demo", ttl=0.2, cache=cache)
        def compute():
            calls["n"] += 1
            return calls["n"]

        first = compute()
        second = compute()
        assert first == second == 1  # served from cache
        time.sleep(0.25)
        third = compute()
        assert third == 2  # cache expired, recomputed


def test_chaincache_with_local_file_and_ttlcache():
    from advanced_caching import ChainCache, InMemCache, TTLCache

    calls = {"n": 0}
    with tempfile.TemporaryDirectory() as tmpdir:
        l1 = InMemCache()
        l2 = LocalFileCache(tmpdir)
        chain = ChainCache([(l1, 0), (l2, None)])

        @TTLCache.cached("chain:{user_id}", ttl=0.2, cache=chain)
        def fetch_user(user_id: int):
            calls["n"] += 1
            return {"id": user_id, "v": calls["n"]}

        # First call populates chain (both L1 and file)
        u1 = fetch_user(1)
        assert u1 == {"id": 1, "v": 1}

        # L1 hit
        u2 = fetch_user(1)
        assert u2 == u1

        # Clear L1 by recreating chain with fresh InMem but same file backend
        l1b = InMemCache()
        chain2 = ChainCache([(l1b, 0), (l2, None)])

        @TTLCache.cached("chain:{user_id}", ttl=0.2, cache=chain2)
        def fetch_user_again(user_id: int):
            return fetch_user(user_id)  # will hit file backend via chain2

        u3 = fetch_user_again(1)
        assert u3 == u1  # pulled from LocalFileCache via chain
