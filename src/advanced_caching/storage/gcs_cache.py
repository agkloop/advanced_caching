from __future__ import annotations

import gzip
import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .utils import CacheEntry
from ..serializers import Serializer, pack_entry, unpack_entry, resolve as _resolve_serializer

try:
    from google.cloud import storage as gcs
except ImportError:  # pragma: no cover - optional
    gcs = None


def _hash_bytes(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


class GCSCache:
    """Google Cloud Storage-backed cache.

    Pass any :class:`~advanced_caching.serializers.Serializer` instance.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        client: Any | None = None,
        serializer: Serializer | None = None,
        compress: bool = True,
        compress_level: int = 6,
        dedupe_writes: bool = False,
    ):
        if gcs is None:
            raise ImportError(
                "google-cloud-storage required for GCSCache. "
                "Install: pip install google-cloud-storage"
            )
        self.bucket_name = bucket
        self.prefix = prefix
        self.client = client or gcs.Client()
        self.bucket = self.client.bucket(bucket)
        self._ser = _resolve_serializer(serializer)
        self.compress = compress
        self.compress_level = compress_level
        self._dedupe_writes = dedupe_writes

    def _make_blob(self, key: str):
        return self.bucket.blob(f"{self.prefix}{key}")

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
        blob = self._make_blob(key)
        try:
            entry = self._decode(blob.download_as_bytes())
            if entry is None:
                return None
            return entry.value if entry.is_fresh() else None
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int | float = 0) -> None:
        now = time.time()
        entry = CacheEntry(
            value=value,
            fresh_until=now + ttl if ttl > 0 else float("inf"),
            created_at=now,
        )
        data = self._encode(entry)
        blob = self._make_blob(key)
        try:
            if self._dedupe_writes:
                try:
                    blob.reload()
                    if blob.metadata and blob.metadata.get("ac-hash") == _hash_bytes(
                        data
                    ):
                        return
                except Exception:
                    pass
            blob.metadata = blob.metadata or {}
            if self._dedupe_writes:
                blob.metadata["ac-hash"] = _hash_bytes(data)
            blob.upload_from_string(data)
        except Exception as e:
            raise RuntimeError(f"GCSCache set failed: {e}")

    def delete(self, key: str) -> None:
        try:
            self._make_blob(key).delete()
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        try:
            self._make_blob(key).reload()
            return True
        except Exception:
            return False

    def get_entry(self, key: str, now: float | None = None) -> CacheEntry | None:
        try:
            return self._decode(self._make_blob(key).download_as_bytes())
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
        data = self._encode(entry)
        blob = self._make_blob(key)
        try:
            if self._dedupe_writes:
                try:
                    blob.reload()
                    if blob.metadata and blob.metadata.get("ac-hash") == _hash_bytes(
                        data
                    ):
                        return
                except Exception:
                    pass
            blob.metadata = blob.metadata or {}
            if self._dedupe_writes:
                blob.metadata["ac-hash"] = _hash_bytes(data)
            blob.upload_from_string(data)
        except Exception as e:
            raise RuntimeError(f"GCSCache set_entry failed: {e}")

    def set_if_not_exists(self, key: str, value: Any, ttl: int | float) -> bool:
        now = time.time()
        entry = CacheEntry(
            value=value,
            fresh_until=now + ttl if ttl > 0 else float("inf"),
            created_at=now,
        )
        try:
            self._make_blob(key).upload_from_string(
                self._encode(entry), if_generation_match=0
            )
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
