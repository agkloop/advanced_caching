from __future__ import annotations

import asyncio
import hashlib

import pytest

from advanced_caching import cache
from advanced_caching._cache import _make_key_fn


class TestSmartKeyGeneration:
    """
    Unit tests for _make_key_fn to ensure robust cache key generation.
    """

    def test_static_key(self):
        """Test static key without placeholders."""

        def func(a, b):
            pass

        key_fn = _make_key_fn("static-key", func)
        assert key_fn(1, 2) == "static-key"
        assert key_fn(a=1, b=2) == "static-key"

    def test_callable_key(self):
        """Test when key is already a callable."""

        def func(a):
            pass

        def my_key_gen(a):
            return f"custom:{a}"

        key_fn = _make_key_fn(my_key_gen, func)
        assert key_fn(1) == "custom:1"

    def test_simple_positional_optimization(self):
        """Test the optimized path for single '{}' placeholder."""

        def func(user_id):
            pass

        key_fn = _make_key_fn("user:{}", func)

        # Positional arg
        assert key_fn(123) == "user:123"

        # Single keyword arg (fallback behavior)
        assert key_fn(user_id=456) == "user:456"

        # No args (returns template)
        assert key_fn() == "user:{}"

    def test_named_placeholder_kwargs(self):
        """Test named placeholder with keyword arguments."""

        def func(user_id):
            pass

        key_fn = _make_key_fn("user:{user_id}", func)
        assert key_fn(user_id=123) == "user:123"

    def test_named_placeholder_positional(self):
        """Test named placeholder with positional arguments (mapped via signature)."""

        def func(user_id, other):
            pass

        key_fn = _make_key_fn("user:{user_id}", func)
        assert key_fn(123, "ignore") == "user:123"

    def test_named_placeholder_defaults(self):
        """Test named placeholder using default values."""

        def func(user_id=999):
            pass

        key_fn = _make_key_fn("user:{user_id}", func)

        # Use default
        assert key_fn() == "user:999"

        # Override default
        assert key_fn(123) == "user:123"

    def test_mixed_args_and_kwargs(self):
        """Test named placeholders with mixed positional and keyword args."""

        def func(a, b, c):
            pass

        key_fn = _make_key_fn("{a}:{b}:{c}", func)

        # a=1 (pos), b=2 (pos), c=3 (kw)
        assert key_fn(1, 2, c=3) == "1:2:3"

    def test_fallback_to_raw_positional(self):
        """Test fallback to raw positional formatting when named formatting fails."""

        # This happens when template uses {} but function has named args,
        # or when optimization check fails (e.g. multiple {})
        def func(a, b):
            pass

        key_fn = _make_key_fn("{}:{}", func)
        assert key_fn(1, 2) == "1:2"

    def test_missing_argument_returns_template(self):
        """Test that missing required arguments returns the raw template."""

        def func(a, b):
            pass

        key_fn = _make_key_fn("key:{a}", func)

        # 'a' is missing from args/kwargs and has no default
        # format() raises KeyError, fallback format(*args) raises IndexError/ValueError
        # Should return template
        assert key_fn(b=2) == "key:{a}"

    def test_extra_kwargs_in_template(self):
        """Test template using kwargs that aren't in function signature (if **kwargs used)."""

        def func(a, **kwargs):
            pass

        key_fn = _make_key_fn("{a}:{extra}", func)

        assert key_fn(1, extra="value") == "1:value"

    def test_complex_positional_no_optimization(self):
        """Test multiple positional placeholders (bypasses optimization)."""

        def func(a, b):
            pass

        key_fn = _make_key_fn("prefix:{}-suffix:{}", func)
        assert key_fn(1, 2) == "prefix:1-suffix:2"

    def test_format_specifiers(self):
        """Test that format specifiers in template work."""

        def func(price):
            pass

        key_fn = _make_key_fn("price:{price:.2f}", func)
        assert key_fn(12.3456) == "price:12.35"

    def test_object_str_representation(self):
        """Test that objects are correctly converted to string in key."""

        class User:
            def __init__(self, id):
                self.id = id

            def __str__(self):
                return f"User({self.id})"

        def func(user):
            pass

        key_fn = _make_key_fn("obj:{user}", func)
        assert key_fn(User(42)) == "obj:User(42)"


class TestCallableKeys:
    """
    Tests for callable cache keys — lambdas, named functions, hashing, and
    end-to-end integration with the @cache decorator.
    """

    # ── _make_key_fn unit tests ─────────────────────────────────────────────

    def test_callable_passthrough(self):
        """Callable is returned as-is and called with the same args as the fn."""

        def func(user_id: int):
            pass

        key_fn = _make_key_fn(lambda user_id: f"u:{user_id}", func)
        assert key_fn(42) == "u:42"
        assert key_fn(0) == "u:0"

    def test_callable_uses_keyword_arg(self):
        """Callable receives kwargs when the decorator is called with keyword args."""

        def func(user_id: int):
            pass

        key_fn = _make_key_fn(lambda user_id: f"u:{user_id}", func)
        assert key_fn(user_id=7) == "u:7"

    def test_callable_multi_arg(self):
        """Callable can combine multiple positional arguments."""

        def func(tenant: str, user_id: int):
            pass

        key_fn = _make_key_fn(lambda tenant, user_id: f"{tenant}:user:{user_id}", func)
        assert key_fn("acme", 99) == "acme:user:99"
        assert key_fn("beta", 1) == "beta:user:1"

    def test_callable_with_varargs(self):
        """Callable using *args / **kwargs receives them directly."""

        def func(*args, **kwargs):
            pass

        key_fn = _make_key_fn(
            lambda *a, **k: f"lang:{k.get('lang', a[0] if a else 'en')}", func
        )
        assert key_fn("fr") == "lang:fr"
        assert key_fn(lang="de") == "lang:de"
        assert key_fn() == "lang:en"  # default

    def test_callable_conditional_key(self):
        """Callable can branch on argument value."""

        def func(resource_id: int, admin: bool = False):
            pass

        def make_key(resource_id: int, admin: bool = False) -> str:
            prefix = "admin" if admin else "public"
            return f"{prefix}:resource:{resource_id}"

        key_fn = _make_key_fn(make_key, func)
        assert key_fn(5, False) == "public:resource:5"
        assert key_fn(5, True) == "admin:resource:5"
        assert key_fn(5, admin=True) == "admin:resource:5"

    def test_callable_hashing_input(self):
        """Callable can hash complex input to produce a short, safe key."""

        def func(query: str):
            pass

        def hashed_key(query: str) -> str:
            digest = hashlib.sha256(query.encode()).hexdigest()[:12]
            return f"search:{digest}"

        key_fn = _make_key_fn(hashed_key, func)
        k1 = key_fn("SELECT * FROM users")
        k2 = key_fn("SELECT * FROM products")
        assert k1.startswith("search:")
        assert k2.startswith("search:")
        assert k1 != k2
        assert key_fn("SELECT * FROM users") == k1  # deterministic

    def test_callable_tuple_or_list_input(self):
        """Callable can serialise a list/tuple argument into a stable key."""

        def func(ids: list):
            pass

        key_fn = _make_key_fn(
            lambda ids: f"batch:{','.join(str(i) for i in sorted(ids))}", func
        )
        assert key_fn([3, 1, 2]) == "key" or key_fn([3, 1, 2]) == "batch:1,2,3"
        # explicit check
        assert key_fn([3, 1, 2]) == "batch:1,2,3"

    def test_callable_named_function(self):
        """Named function (not lambda) works identically."""

        def func(org: str, repo: str):
            pass

        def build_key(org: str, repo: str) -> str:
            return f"gh:{org}/{repo}"

        key_fn = _make_key_fn(build_key, func)
        assert key_fn("acme", "api") == "gh:acme/api"

    # ── End-to-end with @cache decorator ───────────────────────────────────

    def test_callable_key_end_to_end_sync(self):
        """@cache with callable key actually caches distinct keys per argument."""
        calls: list[int] = []

        @cache(60, key=lambda user_id: f"user:{user_id}")
        def get_user(user_id: int) -> dict:
            calls.append(user_id)
            return {"id": user_id}

        get_user(1)
        get_user(1)  # hit
        get_user(2)  # different key
        assert calls == [1, 2], "should call underlying fn once per unique id"

    def test_callable_key_end_to_end_async(self):
        """@cache async with callable key caches correctly."""
        calls: list[int] = []

        @cache(60, key=lambda user_id: f"async_user:{user_id}")
        async def fetch_user(user_id: int) -> dict:
            calls.append(user_id)
            return {"id": user_id}

        async def run():
            await fetch_user(10)
            await fetch_user(10)  # hit
            await fetch_user(20)  # different key

        asyncio.run(run())
        assert calls == [10, 20]

    def test_callable_key_invalidation(self):
        """invalidate() respects callable-key-generated keys."""
        calls: list[int] = []

        @cache(60, key=lambda uid: f"inv_user:{uid}")
        def get_user(uid: int) -> dict:
            calls.append(uid)
            return {"uid": uid}

        get_user(5)
        get_user(5)  # hit — calls still [5]
        get_user.invalidate(5)
        get_user(5)  # miss — re-fetches
        assert calls == [5, 5]

    def test_callable_key_conditional_namespacing(self):
        """Callable key can namespace by environment/context."""
        env = {"name": "staging"}

        @cache(60, key=lambda resource_id: f"{env['name']}:res:{resource_id}")
        def get_resource(resource_id: int) -> str:
            return f"data:{resource_id}"

        r1 = get_resource(1)
        env["name"] = "prod"
        r2 = get_resource(1)  # different key → miss
        assert r1 == "data:1"
        assert r2 == "data:1"

    def test_callable_key_multi_arg_end_to_end(self):
        """Callable key combining multiple args produces correct cache segmentation."""
        calls: list[tuple] = []

        @cache(60, key=lambda tenant, endpoint: f"{tenant}:{endpoint}")
        def api_call(tenant: str, endpoint: str) -> str:
            calls.append((tenant, endpoint))
            return f"{tenant}-{endpoint}"

        api_call("a", "/users")
        api_call("a", "/users")  # hit
        api_call("b", "/users")  # different tenant → miss
        api_call("a", "/orders")  # different endpoint → miss
        assert calls == [("a", "/users"), ("b", "/users"), ("a", "/orders")]
