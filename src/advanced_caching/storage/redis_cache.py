from __future__ import annotations

import math
import time
from typing import Any

from .utils import CacheEntry, Serializer, _BUILTIN_SERIALIZERS

try:
    import redis
except ImportError:  # pragma: no cover - optional
    redis = None  # type: ignore


class RedisCache:
    """Redis-backed cache storage with optional dedupe writes."""

    def __init__(
        self,
        redis_client: Any,
        prefix: str = "",
        serializer: str | Serializer | None = "pickle",
        dedupe_writes: bool = False,
    ):
        if redis is None:
            raise ImportError("redis package required. Install: pip install redis")
        self.client = redis_client
        self.prefix = prefix
        self._serializer, self._wrap_entries = self._resolve_serializer(serializer)
        self._dedupe_writes = dedupe_writes

    @staticmethod
    def _wrap_payload(obj: Any) -> Any:
        if isinstance(obj, CacheEntry):
            return {
                "__ac_type": "entry",
                "v": obj.value,
                "f": obj.fresh_until,
                "c": obj.created_at,
            }
        return {"__ac_type": "value", "v": obj}

    @staticmethod
    def _unwrap_payload(obj: Any) -> Any:
        if isinstance(obj, dict):
            obj_type = obj.get("__ac_type")
            if obj_type == "entry":
                return CacheEntry(
                    value=obj.get("v"),
                    fresh_until=float(obj.get("f", 0.0)),
                    created_at=float(obj.get("c", 0.0)),
                )
            if obj_type == "value":
                return obj.get("v")
        return obj

    def _serialize(self, obj: Any) -> bytes:
        if self._wrap_entries:
            return self._serializer.dumps(self._wrap_payload(obj))
        return self._serializer.dumps(obj)

    def _deserialize(self, data: bytes) -> Any:
        obj = self._serializer.loads(data)
        if self._wrap_entries:
            return self._unwrap_payload(obj)
        return obj

    def _resolve_serializer(
        self, serializer: str | Serializer | None
    ) -> tuple[Serializer, bool]:
        if serializer is None:
            serializer = "pickle"
        if isinstance(serializer, str):
            name = serializer.lower()
            if name not in _BUILTIN_SERIALIZERS:
                raise ValueError("Unsupported serializer. Use 'pickle' or 'json'.")
            serializer_obj = _BUILTIN_SERIALIZERS[name]
            return serializer_obj, not bool(
                getattr(serializer_obj, "handles_entries", False)
            )
        if hasattr(serializer, "dumps") and hasattr(serializer, "loads"):
            wrap = not bool(getattr(serializer, "handles_entries", False))
            return serializer, wrap
        raise TypeError("serializer must be a string or provide dumps/loads methods")

    def _make_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def get(self, key: str) -> Any | None:
        try:
            data = self.client.get(self._make_key(key))
            if data is None:
                return None
            value = self._deserialize(data)
            if isinstance(value, CacheEntry):
                return value.value if value.is_fresh() else None
            return value
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        try:
            data = self._serialize(value)
            if self._dedupe_writes:
                existing = self.client.get(self._make_key(key))
                if existing is not None and existing == data:
                    if ttl > 0:
                        expires = max(1, int(math.ceil(ttl)))
                        self.client.expire(self._make_key(key), expires)
                    return
            if ttl > 0:
                expires = max(1, int(math.ceil(ttl)))
                self.client.setex(self._make_key(key), expires, data)
            else:
                self.client.set(self._make_key(key), data)
        except Exception as e:
            raise RuntimeError(f"Redis set failed: {e}")

    def delete(self, key: str) -> None:
        try:
            self.client.delete(self._make_key(key))
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        try:
            entry = self.get_entry(key)
            if entry is None:
                return False
            return entry.is_fresh()
        except Exception:
            return False

    def get_entry(self, key: str) -> CacheEntry | None:
        try:
            data = self.client.get(self._make_key(key))
            if data is None:
                return None
            value = self._deserialize(data)
            if isinstance(value, CacheEntry):
                return value
            now = time.time()
            return CacheEntry(value=value, fresh_until=float("inf"), created_at=now)
        except Exception:
            return None

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        try:
            data = self._serialize(entry)
            if self._dedupe_writes:
                existing = self.client.get(self._make_key(key))
                if existing is not None and existing == data:
                    if ttl is not None and ttl > 0:
                        expires = max(1, int(math.ceil(ttl)))
                        self.client.expire(self._make_key(key), expires)
                    return
            expires = None
            if ttl is not None and ttl > 0:
                expires = max(1, int(math.ceil(ttl)))
            if expires:
                self.client.setex(self._make_key(key), expires, data)
            else:
                self.client.set(self._make_key(key), data)
        except Exception as e:
            raise RuntimeError(f"Redis set_entry failed: {e}")

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        try:
            data = self._serialize(value)
            expires = None
            if ttl > 0:
                expires = max(1, int(math.ceil(ttl)))
            result = self.client.set(self._make_key(key), data, ex=expires, nx=True)
            return bool(result)
        except Exception:
            return False
