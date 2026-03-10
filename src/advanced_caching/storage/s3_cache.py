from __future__ import annotations

import gzip
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .utils import CacheEntry, CacheStorage
from ..serializers import (
    Serializer,
    pack_entry,
    unpack_entry,
    resolve as _resolve_serializer,
)

try:
    import boto3
except ImportError:  # pragma: no cover - optional
    boto3 = None


def _hash_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


class S3Cache(CacheStorage):
    """S3-backed cache storage.

    Pass any :class:`~advanced_caching.serializers.Serializer` instance.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        s3_client: Any | None = None,
        serializer: Serializer | None = None,
        compress: bool = True,
        compress_level: int = 6,
        dedupe_writes: bool = False,
    ):
        if boto3 is None:
            raise ImportError("boto3 required for S3Cache. Install: pip install boto3")
        self.bucket = bucket
        self.prefix = prefix
        self.client = s3_client or boto3.client("s3")
        self._ser = _resolve_serializer(serializer)
        self.compress = compress
        self.compress_level = compress_level
        self._dedupe_writes = dedupe_writes

    def _make_key(self, key: str) -> str:
        return f"{self.prefix}{key}"

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

    def get(self, key: str) -> Any | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._make_key(key))
            entry = self._decode(obj["Body"].read())
            if entry is None:
                return None
            return entry.value if entry.is_fresh() else None
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int | float = 0) -> None:
        try:
            now = time.time()
            entry = CacheEntry(
                value=value,
                fresh_until=now + ttl if ttl > 0 else float("inf"),
                created_at=now,
            )
            body = self._encode(entry)
            if self._dedupe_writes:
                try:
                    head = self.client.head_object(
                        Bucket=self.bucket, Key=self._make_key(key)
                    )
                    if head.get("Metadata", {}).get("ac-hash") == _hash_bytes(body):
                        return
                except Exception:
                    pass
            kwargs: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": self._make_key(key),
                "Body": body,
            }
            if self._dedupe_writes:
                kwargs["Metadata"] = {"ac-hash": _hash_bytes(body)}
            self.client.put_object(**kwargs)
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

    def get_entry(self, key: str, now: float | None = None) -> CacheEntry | None:
        try:
            obj = self.client.get_object(Bucket=self.bucket, Key=self._make_key(key))
            return self._decode(obj["Body"].read())
        except Exception:
            return None

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
        try:
            body = self._encode(entry)
            if self._dedupe_writes:
                try:
                    head = self.client.head_object(
                        Bucket=self.bucket, Key=self._make_key(key)
                    )
                    if head.get("Metadata", {}).get("ac-hash") == _hash_bytes(body):
                        return
                except Exception:
                    pass
            kwargs: dict[str, Any] = {
                "Bucket": self.bucket,
                "Key": self._make_key(key),
                "Body": body,
            }
            if self._dedupe_writes:
                kwargs["Metadata"] = {"ac-hash": _hash_bytes(body)}
            self.client.put_object(**kwargs)
        except Exception as e:
            raise RuntimeError(f"S3Cache set_entry failed: {e}")

    def set_if_not_exists(self, key: str, value: Any, ttl: int | float) -> bool:
        if self.exists(key):
            return False
        try:
            self.set(key, value, ttl)
            return True
        except Exception:
            return False

    def get_many(self, keys: list[str]) -> dict[str, Any]:
        """Parallel fetch using threads."""
        results: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=min(32, len(keys) + 1)) as ex:
            future_to_key = {ex.submit(self.get, k): k for k in keys}
            for future, k in future_to_key.items():
                try:
                    val = future.result()
                    if val is not None:
                        results[k] = val
                except Exception:
                    pass
        return results

    def set_many(self, mapping: dict[str, Any], ttl: int | float = 0) -> None:
        """Parallel set using threads."""
        with ThreadPoolExecutor(max_workers=min(32, len(mapping) + 1)) as ex:
            ex.map(lambda item: self.set(item[0], item[1], ttl), mapping.items())
