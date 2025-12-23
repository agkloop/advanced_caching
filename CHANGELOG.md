# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2025-12-23

### Changed
- **Major Architecture Overhaul**: The library is now fully async-native.
  - `TTLCache`, `SWRCache`, and `BGCache` now support `async def` functions natively using `await`.
  - Synchronous functions are still supported via intelligent inspection, maintaining backward compatibility.
- **Unified Scheduling**: `SWRCache` (in sync mode) and `BGCache` now use `APScheduler` (`SharedScheduler` and `SharedAsyncScheduler`) for all background tasks, replacing ad-hoc threading.
- **Testing**: Integration tests rewritten to use `pytest-asyncio` with `mode="auto"`.

### Added
- `AsyncTTLCache`, `AsyncStaleWhileRevalidateCache`, `AsyncBackgroundCache` classes (aliased to `TTLCache`, `SWRCache`, `BGCache`).
- `SharedAsyncScheduler` for managing async background jobs.
- `pytest-asyncio` configuration in `pyproject.toml`.

## [0.1.6] - 2025-12-15

### Changed
- `JsonSerializer` now uses `orjson` for significantly faster JSON serialization/deserialization (~2-3x faster)
- `BGCache.register_loader` with `run_immediately=True` now checks if data exists in cache before executing the loader function, avoiding unnecessary function execution when data is already present in Redis/L2 cache.

### Added
- Comprehensive cache rehydration tests for all decorators (TTLCache, SWRCache, BGCache) verifying that existing Redis data is retrieved without re-executing functions.
- 7 new integration tests in `TestCacheRehydration` class covering cache hit and cache miss scenarios for all decorators.

### Performance
- Reduced unnecessary loader executions in BGCache when Redis already contains fresh data.
- Improved JSON serialization performance with orjson integration.

## [0.1.5] - 2025-12-15

### Added
- RedisCache now supports pluggable serializers with built-ins for `pickle` (default) and `json`, plus custom `dumps`/`loads` implementations.
- `HybridCache.from_redis` helper for a one-liner L1 (in-memory) + L2 (Redis) setup.
- `HybridCache` now supports `l2_ttl` parameter for independent L2 TTL control. Defaults to `l1_ttl * 2` if not specified.
- `__version__` attribute exposed in the main module for version checking.
- Comprehensive test coverage for BGCache lambda cache factory pattern and HybridCache l2_ttl behavior.
- Documentation example for using lambda cache factories with BGCache (lazy Redis connection initialization).

## [0.1.4] - 2025-12-12

### Changed
- Performance improvements in hot paths:
  - Reduced repeated cache initialization/lookups inside decorators.
  - Reduced repeated `time.time()` calls by reusing a single timestamp per operation.
  - `CacheEntry` is now a slotted dataclass to reduce per-entry memory/attribute overhead.
- SWR background refresh now uses a shared thread pool (avoids spawning a new thread per refresh).

### Added
- Benchmarking & profiling tooling updates:
  - Benchmarks can be configured via environment variables (e.g. `BENCH_WORK_MS`, `BENCH_RUNS`).
  - Helper to compare JSON benchmark runs in `benchmarks.log`.
  - Tight-loop profiler workload for decorator overhead.

### Documentation
- README updated to reflect current APIs, uv usage, and storage/Redis examples.
- Added step-by-step benchmarking/profiling guide in `docs/benchmarking-and-profiling.md`.

## [0.1.3] - 2025-12-10

### Changed
- Defined clear semantics for `ttl` and `interval_seconds` when set to zero:
  - `TTLCache.cached(..., ttl <= 0)` now acts as a transparent decorator (no caching); the wrapped function is always executed.
  - `SWRCache.cached(..., ttl <= 0)` disables SWR and caching entirely; calls go straight to the wrapped function.
  - `BGCache.register_loader(..., interval_seconds <= 0 or ttl <= 0)` disables background scheduling and caching; the loader is called directly on every invocation.
- Simplified logging strategy to eliminate overhead on hot paths while preserving useful diagnostics:
  - Removed all debug/info logging from cache hit/miss and normal code paths.
  - Retained and refined error logging only for exceptional situations.

### Added
- Error-only logging with structured messages:
  - `SWRCache` logs using `logger.exception` when a background refresh job fails, including the cache key and full traceback.
  - `BGCache` refresh jobs:
    - Invoke the user-provided `on_error` handler first when loader failures occur.
    - Log handler failures with `"BGCache error handler failed for key %r"` including the key and traceback.
    - Log uncaught loader errors with `"BGCache refresh job failed for key %r"` when no `on_error` is supplied.
- New tests ensuring that:
  - `ttl == 0` for TTLCache and SWRCache disables caching (each call executes the function and increments a counter).
  - `interval_seconds == 0` or `ttl == 0` for BGCache disables background loading and caching (each call executes the loader and increments a counter).

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

[Unreleased]: https://github.com/agkloop/advanced_caching/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/agkloop/advanced_caching/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/agkloop/advanced_caching/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/agkloop/advanced_caching/compare/v0.1.1...v0.1.2
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
