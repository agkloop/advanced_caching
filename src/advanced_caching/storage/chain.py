from __future__ import annotations

import time
from typing import Any

from .utils import CacheEntry, CacheStorage


class ChainCache:
    """Composable multi-level cache (L1→L2→...→Ln)."""

    def __init__(self, levels: list[tuple[CacheStorage, int | None]]):
        if not levels:
            raise ValueError("ChainCache requires at least one level")
        self.levels = levels

    def _level_ttl(self, level_ttl: int | None, ttl: int) -> int:
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

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        for cache, lvl_ttl in self.levels:
            cache.set(key, value, self._level_ttl(lvl_ttl, ttl))

    def delete(self, key: str) -> None:
        for cache, _ in self.levels:
            try:
                cache.delete(key)
            except Exception:
                pass

    def exists(self, key: str) -> bool:
        return any(cache.exists(key) for cache, _ in self.levels)

    def get_entry(self, key: str) -> CacheEntry | None:
        hit_entry, hit_index = None, None
        for idx, (cache, lvl_ttl) in enumerate(self.levels):
            if hasattr(cache, "get_entry"):
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
            if hasattr(cache, "set_entry"):
                cache.set_entry(
                    key,
                    hit_entry,
                    ttl=self._level_ttl(
                        lvl_ttl, int(hit_entry.fresh_until - time.time())
                    ),
                )  # type: ignore[attr-defined]
            else:
                cache.set(key, hit_entry.value, self._level_ttl(lvl_ttl, 0))
        return hit_entry

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        for cache, lvl_ttl in self.levels:
            effective_ttl = self._level_ttl(
                lvl_ttl,
                ttl if ttl is not None else int(entry.fresh_until - time.time()),
            )
            if hasattr(cache, "set_entry"):
                cache.set_entry(key, entry, ttl=effective_ttl)  # type: ignore[attr-defined]
            else:
                cache.set(key, entry.value, effective_ttl)

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
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
