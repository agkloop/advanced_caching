# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Planned
- Distributed tracing/observability
- Metrics export (Prometheus)
- Cache warming strategies
- Serialization plugins (msgpack, protobuf)
- Redis cluster support
- DynamoDB backend example

## [0.1.2] - 2025-12-10

### Changed
- Unified public decorator arguments to use consistent names:
  - `TTLCache.cached(key, ttl, cache=None)`
  - `SWRCache.cached(key, ttl, stale_ttl=0, cache=None, enable_lock=True)`
  - `BGCache.register_loader(key, interval_seconds, ttl=None, run_immediately=True, on_error=None, cache=None)`
- Documented and clarified key template behavior across decorators:
  - Positional templates: `"user:{}"` → first positional argument
  - Named templates: `"user:{user_id}"`, `"i18n:{lang}"` → keyword arguments by name
  - Robust key lambdas for default arguments and complex keys.
- Updated README API reference to match current behavior and naming, with:
  - New "Key templates & custom keys" section.
  - Richer examples for TTLCache, SWRCache, and BGCache (sync + async).
  - Clear explanation of how `key`, `ttl`, `stale_ttl`, and `interval_seconds` interact.

### Added
- New edge-case tests for:
  - `InMemCache` (cleanup, lock property, `set_if_not_exists` with expired entries).
  - `HybridCache` (constructor validation, basic get/set/exists/delete behavior).
  - `validate_cache_storage()` failure path.
  - Decorator key-generation edge paths:
    - Static keys without placeholders.
    - No-arg functions with static keys.
    - Templates with positional placeholders but only kwargs passed.
    - Templates with missing named placeholders falling back to raw keys.
- Additional key-template tests for TTLCache and SWRCache:
  - Positional vs named templates.
  - Extra kwargs with named templates.
  - Default-argument handling via `key=lambda *a, **k: ...`.

### Quality
- Increased test coverage from ~70% to ~82%:
  - `decorators.py` coverage improved to ~87%.
  - `storage.py` coverage improved to ~74%.
- Ensured all tests pass under the documented `pyproject.toml` configuration.

[Unreleased]: https://github.com/namshiv2/advanced_caching/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/namshiv2/advanced_caching/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/namshiv2/advanced_caching/releases/tag/v0.1.1

## [0.1.1] - 2025-12-10

### Added
- Initial release of advanced-caching
- TTLCache decorator for time-based caching with configurable key patterns
- SWRCache (StaleWhileRevalidateCache) decorator for serving stale data while refreshing in background
- BGCache (BackgroundCache) decorator for background scheduler-based periodic loading with APScheduler
- InMemCache storage backend: Thread-safe in-memory cache with TTL support
- RedisCache storage backend: Distributed Redis-backed cache for multi-machine setups
- HybridCache storage backend: Two-level L1 (memory) + L2 (Redis) cache
- CacheStorage protocol for type-safe custom backend implementations
- CacheEntry dataclass for accessing cache metadata (TTL, age, freshness)
- validate_cache_storage() utility function for verifying custom implementations
- Full async/sync support for all decorators
- Comprehensive test suite with 18 unit tests (100% passing)
- Four benchmark suites with real-world measurements showing 9,000-75,000x performance gains
- Complete documentation with API reference, examples, and custom storage implementation guide
- Example: FileCache implementation demonstrating custom storage backend
- Six detailed use case examples (Web APIs, databases, configuration, distributed caching, locks)
- PEP 621 compliant project metadata
- MIT License
- Development tools: pytest, pytest-cov, uv build system
- GitHub Actions workflows for automated testing and PyPI publishing

### Features
- **Type-Safe:** Full type hints and docstrings throughout
- **Zero Framework Dependencies:** Works with FastAPI, Flask, Django, or plain Python (only requires APScheduler)
- **Thread-Safe:** Reentrant locks and atomic operations
- **Performance:** 9,000-75,000x faster on cache hits vs no cache
- **Flexible:** Multiple storage backends, composable decorators, custom backend support
- **Production-Ready:** Comprehensive tests, benchmarks, and documentation

[0.1.1]: https://github.com/namshiv2/advanced_caching/releases/tag/v0.1.1
