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

## [0.1.0] - 2025-12-10

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

[0.1.0]: https://github.com/namshiv2/advanced_caching/releases/tag/v0.1.0

