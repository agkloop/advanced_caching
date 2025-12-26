from __future__ import annotations

import time
from typing import Any

from .utils import CacheEntry, CacheStorage


class HybridCache:
    """Two-level cache: L1 (InMem) + L2 (distributed)."""

    def __init__(
        self,
        l1_cache: CacheStorage | None = None,
        l2_cache: CacheStorage | None = None,
        l1_ttl: int = 60,
        l2_ttl: int | None = None,
    ):
        if l2_cache is None:
            raise ValueError("l2_cache is required for HybridCache")
        self.l1 = l1_cache
        self.l2 = l2_cache
        self.l1_ttl = l1_ttl
        self.l2_ttl = l2_ttl if l2_ttl is not None else l1_ttl * 2

    def get(self, key: str) -> Any | None:
        value = self.l1.get(key) if self.l1 else None
        if value is not None:
            return value
        value = self.l2.get(key)
        if value is not None and self.l1:
            self.l1.set(key, value, self.l1_ttl)
        return value

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        if self.l1:
            self.l1.set(key, value, min(ttl, self.l1_ttl) if ttl > 0 else self.l1_ttl)
        l2_ttl = min(ttl, self.l2_ttl) if ttl > 0 else self.l2_ttl
        self.l2.set(key, value, l2_ttl)

    def get_entry(self, key: str) -> CacheEntry | None:
        entry = (
            self.l1.get_entry(key)
            if self.l1 and hasattr(self.l1, "get_entry")
            else None
        )
        if entry is not None:
            return entry
        entry = self.l2.get_entry(key) if hasattr(self.l2, "get_entry") else None
        if entry is not None and self.l1:
            self.l1.set_entry(key, entry, ttl=self.l1_ttl)
            return entry
        value = self.l2.get(key)
        if value is None:
            return None
        now = time.time()
        entry = CacheEntry(
            value=value,
            fresh_until=now + self.l1_ttl if self.l1_ttl > 0 else float("inf"),
            created_at=now,
        )
        if self.l1:
            self.l1.set_entry(key, entry, ttl=self.l1_ttl)
        return entry

    def delete(self, key: str) -> None:
        if self.l1:
            self.l1.delete(key)
        self.l2.delete(key)

    def exists(self, key: str) -> bool:
        return (self.l1.exists(key) if self.l1 else False) or self.l2.exists(key)

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        l2_ttl = min(ttl, self.l2_ttl) if ttl > 0 else self.l2_ttl
        success = self.l2.set_if_not_exists(key, value, l2_ttl)
        if success and self.l1:
            self.l1.set(key, value, min(ttl, self.l1_ttl) if ttl > 0 else self.l1_ttl)
        return success

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        ttl = ttl if ttl is not None else max(int(entry.fresh_until - time.time()), 0)
        l1_ttl = min(ttl, self.l1_ttl) if ttl > 0 else self.l1_ttl
        l2_ttl = min(ttl, self.l2_ttl) if ttl > 0 else self.l2_ttl
        if self.l1:
            self.l1.set_entry(key, entry, ttl=l1_ttl)
        if hasattr(self.l2, "set_entry"):
            self.l2.set_entry(key, entry, ttl=l2_ttl)
        else:
            self.l2.set(key, entry.value, l2_ttl)
