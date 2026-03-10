from __future__ import annotations

import time
from typing import Any

from .utils import CacheEntry, CacheStorage


class ChainCache(CacheStorage):
    """Composable multi-level cache (L1→L2→...→Ln).

    On a cache hit the value is promoted to all faster levels so subsequent
    reads stay in the fastest tier.

    Prefer the :meth:`build` classmethod over the low-level constructor::

        cache = ChainCache.build(InMemCache(), RedisCache(client), ttls=[60, 3600])
    """

    def __init__(self, levels: list[tuple[CacheStorage, int | float | None]]):
        if not levels:
            raise ValueError("ChainCache requires at least one level")
        self.levels = levels
        # Pre-compute capability flags to avoid per-call hasattr overhead.
        self._has_get_entry: list[bool] = [hasattr(c, "get_entry") for c, _ in levels]
        self._has_set_entry: list[bool] = [hasattr(c, "set_entry") for c, _ in levels]
        self._has_clear: list[bool] = [hasattr(c, "clear") for c, _ in levels]

    @classmethod
    def build(
        cls,
        *caches: CacheStorage,
        ttls: list[int | float | None] | None = None,
    ) -> ChainCache:
        """Ergonomic constructor for multi-level caches.

        Args:
            *caches: Cache backends ordered from fastest (L1) to slowest (Ln).
            ttls: Optional per-level TTL overrides (same length as caches).
                  ``None`` entries mean "use the caller-supplied TTL as-is".

        Example::

            cache = ChainCache.build(
                InMemCache(),
                RedisCache(client),
                ttls=[60, 3600],
            )
        """
        if not caches:
            raise ValueError("At least one cache backend is required")
        if ttls is None:
            ttls = [None] * len(caches)
        elif len(ttls) != len(caches):
            raise ValueError(
                f"ttls length ({len(ttls)}) must match number of caches ({len(caches)})"
            )
        return cls(list(zip(caches, ttls)))

    def _level_ttl(
        self, level_ttl: int | float | None, ttl: int | float
    ) -> int | float:
        if level_ttl is None:
            return ttl
        if ttl <= 0:
            return level_ttl
        return min(level_ttl, ttl) if level_ttl > 0 else ttl

    def get(self, key: str) -> Any | None:
        hit_value, hit_index = None, None
        for idx, (cache, lvl_ttl) in enumerate(self.levels):
            value = cache.get(key)
            if value is not None:
                hit_value, hit_index = value, idx
                break
        if hit_value is None:
            return None
        for promote_idx in range(0, hit_index):
            cache, lvl_ttl = self.levels[promote_idx]
            cache.set(key, hit_value, self._level_ttl(lvl_ttl, 0))
        return hit_value

    def set(self, key: str, value: Any, ttl: int | float = 0) -> None:
        for cache, lvl_ttl in self.levels:
            cache.set(key, value, self._level_ttl(lvl_ttl, ttl))

    def delete(self, key: str) -> None:
        for cache, _ in self.levels:
            try:
                cache.delete(key)
            except Exception:
                pass

    def clear(self) -> None:
        """Clear all entries from every level that supports it."""
        for idx, (cache, _) in enumerate(self.levels):
            if self._has_clear[idx]:
                cache.clear()  # type: ignore[union-attr]

    def exists(self, key: str) -> bool:
        return any(cache.exists(key) for cache, _ in self.levels)

    def get_entry(self, key: str, now: float | None = None) -> CacheEntry | None:
        hit_entry, hit_index = None, None
        for idx, (cache, lvl_ttl) in enumerate(self.levels):
            if self._has_get_entry[idx]:
                entry = cache.get_entry(key)  # type: ignore[attr-defined]
            else:
                value = cache.get(key)
                entry = None
                if value is not None:
                    now = time.time()
                    entry = CacheEntry(
                        value=value, fresh_until=float("inf"), created_at=now
                    )
            if entry and entry.is_fresh():
                hit_entry, hit_index = entry, idx
                break
        if hit_entry is None:
            return None
        for promote_idx in range(0, hit_index):
            cache, lvl_ttl = self.levels[promote_idx]
            if self._has_set_entry[promote_idx]:
                cache.set_entry(  # type: ignore[attr-defined]
                    key,
                    hit_entry,
                    ttl=self._level_ttl(lvl_ttl, hit_entry.fresh_until - time.time()),
                )
            else:
                cache.set(key, hit_entry.value, self._level_ttl(lvl_ttl, 0))
        return hit_entry

    def set_entry(
        self, key: str, entry: CacheEntry, ttl: int | float | None = None
    ) -> None:
        for idx, (cache, lvl_ttl) in enumerate(self.levels):
            effective_ttl = self._level_ttl(
                lvl_ttl,
                ttl if ttl is not None else entry.fresh_until - time.time(),
            )
            if self._has_set_entry[idx]:
                cache.set_entry(key, entry, ttl=effective_ttl)  # type: ignore[attr-defined]
            else:
                cache.set(key, entry.value, effective_ttl)

    def set_if_not_exists(self, key: str, value: Any, ttl: int | float) -> bool:
        *upper_levels, deepest = self.levels[:-1], self.levels[-1]
        deep_cache, deep_ttl = deepest
        deep_success = deep_cache.set_if_not_exists(
            key, value, self._level_ttl(deep_ttl, ttl)
        )
        if not deep_success:
            return False
        for cache, lvl_ttl in upper_levels:  # type: ignore[misc]
            cache.set(key, value, self._level_ttl(lvl_ttl, ttl))
        return True
