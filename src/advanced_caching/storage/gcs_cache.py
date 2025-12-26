from __future__ import annotations

import gzip
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from .utils import CacheEntry, Serializer, _BUILTIN_SERIALIZERS, _hash_bytes

try:
    from google.cloud import storage as gcs
except ImportError:  # pragma: no cover - optional
    gcs = None


class GCSCache:
    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        client: Any | None = None,
        serializer: str | Serializer | None = "pickle",
        compress: bool = True,
        compress_level: int = 6,
        dedupe_writes: bool = False,
    ):
        if gcs is None:
            raise ImportError(
                "google-cloud-storage required for GCSCache. Install: pip install google-cloud-storage"
            )
        self.bucket_name = bucket
        self.prefix = prefix
        self.client = client or gcs.Client()
        self.bucket = self.client.bucket(bucket)
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

    def _make_blob(self, key: str):
        path = f"{self.prefix}{key}"
        return self.bucket.blob(path)

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
        blob = self._make_blob(key)
        try:
            data = blob.download_as_bytes()
            value = self._deserialize(data)
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
        blob = self._make_blob(key)
        import time

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
        data = self._serialize(payload)
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
        blob = self._make_blob(key)
        try:
            blob.delete()
        except Exception:
            pass

    def exists(self, key: str) -> bool:
        blob = self._make_blob(key)
        try:
            blob.reload()
            return True
        except Exception:
            return False

    def get_entry(self, key: str) -> CacheEntry | None:
        blob = self._make_blob(key)
        try:
            data = blob.download_as_bytes()
            value = self._deserialize(data)
            if isinstance(value, dict) and value.get("__ac_type") == "entry":
                entry = CacheEntry(
                    value=value.get("v"),
                    fresh_until=float(value.get("f", 0.0)),
                    created_at=float(value.get("c", 0.0)),
                )
                return entry
            import time

            now = time.time()
            return CacheEntry(value=value, fresh_until=float("inf"), created_at=now)
        except Exception:
            return None

    def set_entry(self, key: str, entry: CacheEntry, ttl: int | None = None) -> None:
        import time

        if ttl is not None:
            now = time.time()
            entry = CacheEntry(value=entry.value, fresh_until=now + ttl, created_at=now)
        payload = {
            "__ac_type": "entry",
            "v": entry.value,
            "f": entry.fresh_until,
            "c": entry.created_at,
        }
        data = self._serialize(payload)
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

    def set_if_not_exists(self, key: str, value: Any, ttl: int) -> bool:
        blob = self._make_blob(key)
        try:
            blob.upload_from_string(self._serialize(value), if_generation_match=0)
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
