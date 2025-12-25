import pytest
from advanced_caching.decorators import _create_smart_key_fn


class TestSmartKeyGeneration:
    """
    Unit tests for _create_smart_key_fn to ensure robust cache key generation.
    """

    def test_static_key(self):
        """Test static key without placeholders."""

        def func(a, b):
            pass

        key_fn = _create_smart_key_fn("static-key", func)
        assert key_fn(1, 2) == "static-key"
        assert key_fn(a=1, b=2) == "static-key"

    def test_callable_key(self):
        """Test when key is already a callable."""

        def func(a):
            pass

        def my_key_gen(a):
            return f"custom:{a}"

        key_fn = _create_smart_key_fn(my_key_gen, func)
        assert key_fn(1) == "custom:1"

    def test_simple_positional_optimization(self):
        """Test the optimized path for single '{}' placeholder."""

        def func(user_id):
            pass

        key_fn = _create_smart_key_fn("user:{}", func)

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

        key_fn = _create_smart_key_fn("user:{user_id}", func)
        assert key_fn(user_id=123) == "user:123"

    def test_named_placeholder_positional(self):
        """Test named placeholder with positional arguments (mapped via signature)."""

        def func(user_id, other):
            pass

        key_fn = _create_smart_key_fn("user:{user_id}", func)
        assert key_fn(123, "ignore") == "user:123"

    def test_named_placeholder_defaults(self):
        """Test named placeholder using default values."""

        def func(user_id=999):
            pass

        key_fn = _create_smart_key_fn("user:{user_id}", func)

        # Use default
        assert key_fn() == "user:999"

        # Override default
        assert key_fn(123) == "user:123"

    def test_mixed_args_and_kwargs(self):
        """Test named placeholders with mixed positional and keyword args."""

        def func(a, b, c):
            pass

        key_fn = _create_smart_key_fn("{a}:{b}:{c}", func)

        # a=1 (pos), b=2 (pos), c=3 (kw)
        assert key_fn(1, 2, c=3) == "1:2:3"

    def test_fallback_to_raw_positional(self):
        """Test fallback to raw positional formatting when named formatting fails."""

        # This happens when template uses {} but function has named args,
        # or when optimization check fails (e.g. multiple {})
        def func(a, b):
            pass

        key_fn = _create_smart_key_fn("{}:{}", func)
        assert key_fn(1, 2) == "1:2"

    def test_missing_argument_returns_template(self):
        """Test that missing required arguments returns the raw template."""

        def func(a, b):
            pass

        key_fn = _create_smart_key_fn("key:{a}", func)

        # 'a' is missing from args/kwargs and has no default
        # format() raises KeyError, fallback format(*args) raises IndexError/ValueError
        # Should return template
        assert key_fn(b=2) == "key:{a}"

    def test_extra_kwargs_in_template(self):
        """Test template using kwargs that aren't in function signature (if **kwargs used)."""

        def func(a, **kwargs):
            pass

        key_fn = _create_smart_key_fn("{a}:{extra}", func)

        assert key_fn(1, extra="value") == "1:value"

    def test_complex_positional_no_optimization(self):
        """Test multiple positional placeholders (bypasses optimization)."""

        def func(a, b):
            pass

        key_fn = _create_smart_key_fn("prefix:{}-suffix:{}", func)
        assert key_fn(1, 2) == "prefix:1-suffix:2"

    def test_format_specifiers(self):
        """Test that format specifiers in template work."""

        def func(price):
            pass

        key_fn = _create_smart_key_fn("price:{price:.2f}", func)
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

        key_fn = _create_smart_key_fn("obj:{user}", func)
        assert key_fn(User(42)) == "obj:User(42)"
