from __future__ import annotations

import gzip
import os
import shutil
import time
from pathlib import Path
from typing import Any

from .utils import CacheEntry, CacheStorage
from ..serializers import (
    Serializer,
    pack_entry,
    unpack_entry,
    resolve as _resolve_serializer,
)


class LocalFileCache(CacheStorage):
    """Filesystem-backed cache with TTL, optional compression, and atomic writes.

    Pass any :class:`~advanced_caching.serializers.Serializer` instance.

    Example::

        from advanced_caching import serializers, LocalFileCache

        store = LocalFileCache("/tmp/mycache", serializer=serializers.json)
    """

    def __init__(
        self,
        root_dir: str | Path,
        serializer: Serializer | None = None,
        compress: bool = True,
        compress_level: int = 6,
        dedupe_writes: bool = False,
    ):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self._ser = _resolve_serializer(serializer)
        self.compress = compress
        self.compress_level = compress_level
        self._dedupe_writes = dedupe_writes

    def _path(self, key: str) -> Path:
        safe = key.replace(os.sep, "_").replace("/", "_").replace("..", "_")
        return self.root / safe

    def _encode(self, entry: CacheEntry) -> bytes:
        data = pack_entry(entry, self._ser)
        return (
            gzip.compress(data, compresslevel=self.compress_level)
            if self.compress
            else data
        )

    def _decode(self, raw: bytes) -> CacheEntry | None:
        try:
            data = gzip.decompress(raw) if self.compress else raw
            return unpack_entry(data, self._ser)
        except Exception:
            return None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def get_entry(self, key: str, now: float | None = None) -> CacheEntry | None:
        path = self._path(key)
        if not path.exists():
            return None
        entry = self._decode(path.read_bytes())
        if entry is None or not entry.is_fresh():
            try:
                path.unlink()
            except Exception:
                pass
            return None
        return entry

    def get(self, key: str) -> Any | None:
        entry = self.get_entry(key)
        return entry.value if entry is not None else None

    def set(self, key: str, value: Any, ttl: int | float = 0) -> None:
        now = time.time()
        entry = CacheEntry(
            value=value,
            fresh_until=now + ttl if ttl > 0 else float("inf"),
            created_at=now,
        )
        data = self._encode(entry)
        path = self._path(key)
        if self._dedupe_writes and path.exists():
            try:
                if path.read_bytes() == data:
                    return
            except Exception:
                pass
        self._atomic_write(path, data)

    def delete(self, key: str) -> None:
        try:
            self._path(key).unlink()
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        return self.get_entry(key) is not None

    def set_entry(
        self, key: str, entry: CacheEntry, ttl: int | float | None = None
    ) -> None:
        if ttl is not None:
            now = time.time()
            entry = CacheEntry(
                value=entry.value,
                fresh_until=now + ttl if ttl > 0 else float("inf"),
                created_at=now,
            )
        data = self._encode(entry)
        path = self._path(key)
        if self._dedupe_writes and path.exists():
            try:
                if path.read_bytes() == data:
                    return
            except Exception:
                pass
        self._atomic_write(path, data)

    def set_if_not_exists(self, key: str, value: Any, ttl: int | float) -> bool:
        if self.get_entry(key) is not None:
            return False
        self.set(key, value, ttl)
        return True

    def clear(self) -> None:
        """Delete all cache files in the root directory."""
        try:
            for path in self.root.iterdir():
                try:
                    if path.is_file():
                        path.unlink()
                    elif path.is_dir():
                        shutil.rmtree(path)
                except Exception:
                    pass
        except Exception:
            pass
