from __future__ import annotations

import gzip
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .utils import CacheEntry, Serializer, _BUILTIN_SERIALIZERS, _hash_bytes

try:
    import boto3
except ImportError:  # pragma: no cover - optional
    boto3 = None


class S3Cache:
    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        s3_client: Any | None = None,
        serializer: str | Serializer | None = "pickle",
        compress: bool = True,
        compress_level: int = 6,
        dedupe_writes: bool = False,
    ):
        if boto3 is None:
            raise ImportError("boto3 required for S3Cache. Install: pip install boto3")
        self.bucket = bucket
        self.prefix = prefix
        self.client = s3_client or boto3.client("s3")
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

    def _make_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

    def _serialize(self, value: Any) -> bytes:
        data = self.serializer.dumps(value)
        if self.compress:
            return gzip.compress(data, compresslevel=self.compress_level)
        return data

    def _deserialize(self, data: bytes) -> Any:
        if self.compress:
            data = gzip.decompress(data)
        return self.serializer.loads(data)

    def get(self, key: str) -> Any | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._make_key(key))
            body = obj["Body"].read()
            value = self._deserialize(body)
            if isinstance(value, dict) and value.get("__ac_type") == "entry":
                entry = CacheEntry(
                    value=value.get("v"),
                    fresh_until=float(value.get("f", 0.0)),
                    created_at=float(value.get("c", 0.0)),
                )
                return entry.value if entry.is_fresh() else None
            return value
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = 0) -> None:
        try:
            now = time.time()
            entry: CacheEntry | None = None
            if isinstance(value, CacheEntry):
                entry = value
            elif ttl != 0:
                entry = CacheEntry(value=value, fresh_until=now + ttl, created_at=now)

            payload = (
                {
                    "__ac_type": "entry",
                    "v": entry.value,
                    "f": entry.fresh_until,
                    "c": entry.created_at,
                }
                if entry
                else value
            )
            body = self._serialize(payload)

            if self._dedupe_writes:
                try:
                    head = self.client.head_object(
                        Bucket=self.bucket, Key=self._make_key(key)
                    )
                    if head and head.get("Metadata", {}).get("ac-hash") == _hash_bytes(
                        body
                    ):
                        return
                except Exception:
                    pass
            put_kwargs = {
                "Bucket": self.bucket,
                "Key": self._make_key(key),
                "Body": body,
            }
            if self._dedupe_writes:
                put_kwargs["Metadata"] = {"ac-hash": _hash_bytes(body)}
            self.client.put_object(**put_kwargs)
        except Exception as e:
            raise RuntimeError(f"S3Cache set failed: {e}")

    def delete(self, key: str) -> None:
        try:
            self.client.delete_object(Bucket=self.bucket, Key=self._make_key(key))
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._make_key(key))
            return True
        except Exception:
            return False

    def get_entry(self, key: str) -> CacheEntry | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._make_key(key))
            body = obj["Body"].read()
            value = self._deserialize(body)
            if isinstance(value, dict) and value.get("__ac_type") == "entry":
                entry = CacheEntry(
                    value=value.get("v"),
                    fresh_until=float(value.get("f", 0.0)),
                    created_at=float(value.get("c", 0.0)),
                )
                return entry
            now = time.time()
            return CacheEntry(value=value, fresh_until=float("inf"), created_at=now)
        except Exception:
            return None

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        if ttl is not None:
            now = time.time()
            entry = CacheEntry(value=entry.value, fresh_until=now + ttl, created_at=now)
        payload = {
            "__ac_type": "entry",
            "v": entry.value,
            "f": entry.fresh_until,
            "c": entry.created_at,
        }
        try:
            body = self._serialize(payload)
            if self._dedupe_writes:
                try:
                    head = self.client.head_object(
                        Bucket=self.bucket, Key=self._make_key(key)
                    )
                    if head and head.get("Metadata", {}).get("ac-hash") == _hash_bytes(
                        body
                    ):
                        return
                except Exception:
                    pass
            put_kwargs = {
                "Bucket": self.bucket,
                "Key": self._make_key(key),
                "Body": body,
            }
            if self._dedupe_writes:
                put_kwargs["Metadata"] = {"ac-hash": _hash_bytes(body)}
            self.client.put_object(**put_kwargs)
        except Exception as e:
            raise RuntimeError(f"S3Cache set_entry failed: {e}")

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        if self.exists(key):
            return False
        try:
            self.set(key, value, ttl)
            return True
        except Exception:
            return False

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Parallel fetch using threads."""
        results = {}
        with ThreadPoolExecutor(max_workers=min(32, len(keys) + 1)) as executor:
            future_to_key = {executor.submit(self.get, key): key for key in keys}
            for future in future_to_key:
                key = future_to_key[future]
                try:
                    val = future.result()
                    if val is not None:
                        results[key] = val
                except Exception:
                    pass
        return results

    def set_many(self, mapping: dict[str, Any], ttl: int = 0) -> None:
        """Parallel set using threads."""
        with ThreadPoolExecutor(max_workers=min(32, len(mapping) + 1)) as executor:
            executor.map(lambda item: self.set(item[0], item[1], ttl), mapping.items())
