"""Serialization layer for cache backends.

Public symbols:
    Serializer       — protocol (duck-typed, no ABC overhead)
    pack_entry       — encode a CacheEntry to bytes
    unpack_entry     — decode bytes back to a CacheEntry
    pickle           — PickleSerializer singleton (handles full entry natively)
    json             — JsonSerializer singleton (orjson, value-only)
    msgpack          — MsgpackSerializer singleton (requires pip install msgpack)
    protobuf(cls)    — factory: returns a serializer for a protobuf message class

Usage::

    from advanced_caching import serializers, RedisCache

    store = RedisCache(client, serializer=serializers.json)
    store = RedisCache(client, serializer=serializers.msgpack)
    store = RedisCache(client, serializer=serializers.protobuf(MyProtoMessage))
"""

from __future__ import annotations

import pickle as _pickle_mod
import struct
from typing import Any, Protocol, runtime_checkable

from .storage.utils import CacheEntry

# ──────────────────────────────────────────────────────────────────────────────
# Protocol
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class Serializer(Protocol):
    """Minimal serialization contract.

    Optional class attribute ``handles_entries: bool`` controls how
    :func:`pack_entry` / :func:`unpack_entry` treat the serializer:

    * ``True``  — the serializer encodes the full :class:`~storage.utils.CacheEntry`
      object (e.g. Pickle).  No binary header is prepended.
    * ``False`` (default) — the serializer encodes only the *value*.  A 16-byte
      binary header containing ``(fresh_until, created_at)`` as two 64-bit floats
      is prepended so any serializer can be used without a custom schema.
    """

    def dumps(self, obj: Any) -> bytes: ...
    def loads(self, data: bytes) -> Any: ...


# ──────────────────────────────────────────────────────────────────────────────
# pack / unpack — single place that knows the wire format
# ──────────────────────────────────────────────────────────────────────────────

# Header: two 64-bit big-endian floats → (fresh_until, created_at) = 16 bytes
_HDR = struct.Struct(">dd")
_HDR_SIZE = _HDR.size  # 16


def pack_entry(entry: CacheEntry, s: Serializer) -> bytes:
    """Serialize *entry* to bytes using serializer *s*.

    Serializers with ``handles_entries=True`` encode the whole entry in one
    pass.  All others receive the value only; metadata is prepended as a
    compact 16-byte struct header so **any** serializer works out of the box.
    """
    if getattr(s, "handles_entries", False):
        return s.dumps(entry)
    return _HDR.pack(entry.fresh_until, entry.created_at) + s.dumps(entry.value)


def unpack_entry(data: bytes, s: Serializer) -> CacheEntry:
    """Deserialize bytes produced by :func:`pack_entry` back to a CacheEntry."""
    if getattr(s, "handles_entries", False):
        return s.loads(data)
    fresh_until, created_at = _HDR.unpack_from(data)
    value = s.loads(data[_HDR_SIZE:])
    return CacheEntry(value=value, fresh_until=fresh_until, created_at=created_at)


# ──────────────────────────────────────────────────────────────────────────────
# Built-in serializers
# ──────────────────────────────────────────────────────────────────────────────


class PickleSerializer:
    """Pickle — serializes the full CacheEntry in one shot, no header needed."""

    __slots__ = ()
    handles_entries: bool = True

    @staticmethod
    def dumps(obj: Any) -> bytes:
        return _pickle_mod.dumps(obj, protocol=_pickle_mod.HIGHEST_PROTOCOL)

    @staticmethod
    def loads(data: bytes) -> Any:
        return _pickle_mod.loads(data)


class JsonSerializer:
    """JSON via orjson — value only; entry metadata lives in the binary header."""

    __slots__ = ()
    handles_entries: bool = False

    @staticmethod
    def dumps(obj: Any) -> bytes:
        import orjson  # lazy — avoids hard dep at module import time

        return orjson.dumps(obj)

    @staticmethod
    def loads(data: bytes) -> Any:
        import orjson

        return orjson.loads(data)


class MsgpackSerializer:
    """msgpack — value only; entry metadata lives in the binary header.

    Requires ``pip install msgpack``.
    """

    __slots__ = ()
    handles_entries: bool = False

    @staticmethod
    def dumps(obj: Any) -> bytes:
        try:
            import msgpack
        except ImportError as exc:
            raise ImportError("msgpack required: pip install msgpack") from exc
        return msgpack.packb(obj, use_bin_type=True)

    @staticmethod
    def loads(data: bytes) -> Any:
        try:
            import msgpack
        except ImportError as exc:
            raise ImportError("msgpack required: pip install msgpack") from exc
        return msgpack.unpackb(data, raw=False)


def protobuf(message_class: type) -> Serializer:
    """Return a serializer for a protobuf *message_class*.

    The class must expose ``SerializeToString()`` / ``FromString(data)``,
    which all generated protobuf classes do.

    Example::

        from myproto import UserMessage
        store = RedisCache(client, serializer=serializers.protobuf(UserMessage))

    The entry metadata (TTL timestamps) is stored in the 16-byte binary header
    prepended by :func:`pack_entry`; the protobuf bytes contain only the value.
    """

    class _Protobuf:
        __slots__ = ("_cls",)
        handles_entries: bool = False

        def __init__(self, cls: type) -> None:
            self._cls = cls

        def dumps(self, obj: Any) -> bytes:
            return obj.SerializeToString()

        def loads(self, data: bytes) -> Any:
            return self._cls.FromString(data)

    return _Protobuf(message_class)


# ──────────────────────────────────────────────────────────────────────────────
# Module-level singletons — use these directly
# ──────────────────────────────────────────────────────────────────────────────

pickle: PickleSerializer = PickleSerializer()
json: JsonSerializer = JsonSerializer()
msgpack: MsgpackSerializer = MsgpackSerializer()

_ALIASES: dict[str, Serializer] = {
    "pickle": pickle,
    "json": json,
    "msgpack": msgpack,
}


def resolve(s: "Serializer | str | None") -> "Serializer":
    """Resolve a serializer from an instance, string alias, or ``None`` (→ pickle).

    Accepted values:
    * ``None``        → :data:`pickle`
    * ``"json"``      → :data:`json`
    * ``"msgpack"``   → :data:`msgpack`
    * ``"pickle"``    → :data:`pickle`
    * Any object with ``dumps``/``loads`` methods.
    """
    if s is None:
        return pickle
    if isinstance(s, str):
        try:
            return _ALIASES[s]
        except KeyError:
            raise ValueError(
                f"Unknown serializer alias {s!r}. "
                f"Valid aliases: {list(_ALIASES)}"
            ) from None
    if callable(getattr(s, "dumps", None)) and callable(getattr(s, "loads", None)):
        return s
    raise TypeError("serializer must expose dumps/loads methods")
