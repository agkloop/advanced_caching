import os
import time
import tempfile

from advanced_caching import LocalFileCache, cache, ChainCache, InMemCache


def test_local_file_cache_set_get_and_expiry():
    with tempfile.TemporaryDirectory() as tmpdir:
        c = LocalFileCache(tmpdir)
        c.set("foo", "bar", ttl=0.1)
        assert c.get("foo") == "bar"
        time.sleep(0.2)
        assert c.get("foo") is None


def test_local_file_cache_dedupe_writes():
    with tempfile.TemporaryDirectory() as tmpdir:
        c = LocalFileCache(tmpdir, dedupe_writes=True)
        c.set("foo", {"a": 1}, ttl=0)
        mtime1 = os.path.getmtime(os.path.join(tmpdir, "foo"))
        time.sleep(0.05)
        c.set("foo", {"a": 1}, ttl=0)
        mtime2 = os.path.getmtime(os.path.join(tmpdir, "foo"))
        assert c.get("foo") == {"a": 1}
        assert mtime2 <= mtime1 + 0.1
        c.set("foo", {"a": 2}, ttl=0)
        mtime3 = os.path.getmtime(os.path.join(tmpdir, "foo"))
        assert mtime3 > mtime2


def test_ttlcache_with_local_file_cache_decorator():
    calls = {"n": 0}
    with tempfile.TemporaryDirectory() as tmpdir:
        file_store = LocalFileCache(tmpdir)

        @cache(0.2, key="demo", store=file_store)
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
    calls = {"n": 0}
    with tempfile.TemporaryDirectory() as tmpdir:
        l1 = InMemCache()
        l2 = LocalFileCache(tmpdir)
        chain = ChainCache([(l1, 0), (l2, None)])

        @cache(0.2, key="chain:{user_id}", store=chain)
        def fetch_user(user_id: int):
            calls["n"] += 1
            return {"id": user_id, "v": calls["n"]}

        u1 = fetch_user(1)
        assert u1 == {"id": 1, "v": 1}

        # L1 hit
        u2 = fetch_user(1)
        assert u2 == u1

        # Clear L1, rebuild chain — file backend still has the value
        l1b = InMemCache()
        chain2 = ChainCache([(l1b, 0), (l2, None)])

        @cache(0.2, key="chain:{user_id}", store=chain2)
        def fetch_user_again(user_id: int):
            calls["n"] += 1
            return {"id": user_id, "v": calls["n"]}

        u3 = fetch_user_again(1)
        assert u3 == u1  # pulled from LocalFileCache via chain2
