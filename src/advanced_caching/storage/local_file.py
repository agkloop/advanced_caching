from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import gzip

from .utils import CacheEntry, Serializer, _BUILTIN_SERIALIZERS


class LocalFileCache:
    """Filesystem-backed cache with TTL and optional dedupe."""

    def __init__(
        self,
        root_dir: str | Path,
        serializer: str | Serializer | None = "pickle",
        compress: bool = True,
        compress_level: int = 6,
        dedupe_writes: bool = False,
    ):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.compress = compress
        self.compress_level = compress_level
        self.serializer = self._resolve_serializer(serializer)
        self._dedupe_writes = dedupe_writes

    def _resolve_serializer(self, serializer: str | Serializer | None) -> Serializer:
        if serializer is None:
            serializer = "pickle"
        if isinstance(serializer, str):
            name = serializer.lower()
            if name not in _BUILTIN_SERIALIZERS:
                raise ValueError("Unsupported serializer. Use 'pickle' or 'json'.")
            return _BUILTIN_SERIALIZERS[name]
        if hasattr(serializer, "dumps") and hasattr(serializer, "loads"):
            return serializer
        raise TypeError("serializer must be a string or provide dumps/loads methods")

    def _path(self, key: str) -> Path:
        return self.root / key

    def _serialize_entry(self, entry: CacheEntry) -> bytes:
        payload = {
            "__ac_type": "entry",
            "v": entry.value,
            "f": entry.fresh_until,
            "c": entry.created_at,
        }
        data = self.serializer.dumps(payload)
        if self.compress:
            data = gzip.compress(data, compresslevel=self.compress_level)
        return data

    def _deserialize_entry(self, data: bytes) -> CacheEntry | None:
        try:
            if self.compress:
                data = gzip.decompress(data)
            payload = self.serializer.loads(data)
            if isinstance(payload, CacheEntry):
                return payload
            if isinstance(payload, dict) and payload.get("__ac_type") == "entry":
                return CacheEntry(
                    value=payload.get("v"),
                    fresh_until=float(payload.get("f", 0.0)),
                    created_at=float(payload.get("c", 0.0)),
                )
            now = time.time()
            return CacheEntry(value=payload, fresh_until=float("inf"), created_at=now)
        except Exception:
            return None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "wb") as tmp:
            tmp.write(data)
        os.replace(tmp_path, path)

    def get_entry(self, key: str) -> CacheEntry | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            entry = self._deserialize_entry(path.read_bytes())
        except Exception:
            return None
        if entry is None:
            return None
        if not entry.is_fresh():
            try:
                path.unlink()
            except Exception:
                pass
            return None
        return entry

    def get(self, key: str) -> Any | None:
        entry = self.get_entry(key)
        return entry.value if entry is not None else None

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        now = time.time()
        fresh_until = now + ttl if ttl > 0 else float("inf")
        entry = CacheEntry(value=value, fresh_until=fresh_until, created_at=now)
        data = self._serialize_entry(entry)
        path = self._path(key)
        if self._dedupe_writes and ttl <= 0 and path.exists():
            try:
                existing_entry = self.get_entry(key)
                if existing_entry is not None and existing_entry.value == value:
                    return
            except Exception:
                pass
        self._atomic_write(path, data)

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            path.unlink()
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        return self.get_entry(key) is not None

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        now = time.time()
        if ttl is not None:
            entry = CacheEntry(
                value=entry.value,
                fresh_until=now + ttl if ttl > 0 else float("inf"),
                created_at=now,
            )
        data = self._serialize_entry(entry)
        path = self._path(key)
        if self._dedupe_writes and ttl is not None and ttl <= 0 and path.exists():
            try:
                existing_entry = self.get_entry(key)
                if existing_entry is not None and existing_entry.value == entry.value:
                    return
            except Exception:
                pass
        self._atomic_write(path, data)

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        existing = self.get_entry(key)
        if existing is not None:
            return False
        self.set(key, value, ttl)
        return True
