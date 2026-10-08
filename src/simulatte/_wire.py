"""Wire values and canonical MessagePack encoding (private).

A *wire value* is what event payloads, trace records and digest projections are made of: ``None``,
``bool``, ``int`` within +/-(2**53 - 1), ``float`` (float64, including infinities and NaN), ``str``,
tuples of wire values and :class:`FrozenMap` of wire values keyed by ``str``.

Map keys are escaped on the wire so that every decoder, including a JavaScript one, can rebuild maps
safely: a key that is ``__proto__``, ``constructor`` or ``prototype``, or that starts with ``~``, is
prefixed with ``~``. Decoders strip one leading ``~``.

:func:`canonical_pack` is the byte-stable form used for digests: map keys sorted by the UTF-8 bytes of
the escaped key, floats always float64, NaN normalized, integers in their smallest MessagePack form.
:func:`pack` uses the same escaping but keeps insertion order.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any, TypeAlias

import msgpack

__all__ = [
    "MAX_SAFE_INT",
    "FrozenMap",
    "Wire",
    "canonical_pack",
    "escape_key",
    "freeze",
    "pack",
    "unescape_key",
    "unpack",
]

MAX_SAFE_INT = 2**53 - 1

_HOSTILE_KEYS = frozenset({"__proto__", "constructor", "prototype"})
_NAN = float("nan")

Wire: TypeAlias = "None | bool | int | float | str | tuple[Wire, ...] | FrozenMap"


class FrozenMap(Mapping[str, "Wire"]):
    """Immutable, hashable mapping from ``str`` to wire values."""

    __slots__ = ("_data", "_hash")

    def __init__(self, data: Mapping[str, Wire]) -> None:
        self._data: dict[str, Wire] = dict(data)
        self._hash: int | None = None

    def __getitem__(self, key: str) -> Wire:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __hash__(self) -> int:
        if self._hash is None:
            self._hash = hash(frozenset(self._data.items()))
        return self._hash

    def __repr__(self) -> str:
        return f"FrozenMap({self._data!r})"


def freeze(value: object) -> Wire:
    """Return `value` as an immutable wire value.

    Lists and tuples become tuples, mappings become :class:`FrozenMap`. Raises `TypeError` for anything
    that is not a wire value (including non-``str`` keys and ``bytes``) and `OverflowError` for integers
    outside +/-(2**53 - 1).
    """
    if value is None or isinstance(value, (bool, float, str)):
        return value
    if isinstance(value, int):
        _check_int(value)
        return value
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, Mapping):
        return FrozenMap({_check_key(k): freeze(v) for k, v in value.items()})
    raise TypeError(f"not a wire value: {type(value).__name__}")


def escape_key(k: str) -> str:
    """Escape a map key for the wire."""
    if k in _HOSTILE_KEYS or k.startswith("~"):
        return "~" + k
    return k


def unescape_key(k: str) -> str:
    """Invert :func:`escape_key`."""
    if k.startswith("~"):
        return k[1:]
    return k


def canonical_pack(value: Wire) -> bytes:
    """Encode `value` to byte-stable MessagePack (see module docstring)."""
    return _packb(_prepare(value, canonical=True))


def pack(value: Wire) -> bytes:
    """Encode `value` to MessagePack with escaped keys, without sorting."""
    return _packb(_prepare(value, canonical=False))


def unpack(data: bytes, *, max_depth: int = 64, max_len: int = 10**7) -> Wire:
    """Decode MessagePack `data` into a wire value, enforcing limits.

    `max_depth` bounds container nesting and `max_len` bounds the length of any array or map. Raises
    `ValueError` for malformed or truncated input, trailing data, limit violations and values outside the
    wire model (``bytes``, extension types, non-``str`` keys, integers beyond +/-(2**53 - 1)).
    """
    result = msgpack.unpackb(
        data,
        raw=False,
        use_list=False,
        strict_map_key=True,
        max_array_len=max_len,
        max_map_len=max_len,
        object_pairs_hook=_decode_map,
    )
    _validate(result, max_depth)
    return result


def _check_int(value: int) -> None:
    if not -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
        raise OverflowError(f"integer outside +/-(2**53 - 1): {value}")


def _check_key(key: object) -> str:
    if not isinstance(key, str):
        raise TypeError(f"map keys must be str, got {type(key).__name__}")
    return key


def _packb(obj: object) -> bytes:
    return msgpack.packb(obj, use_bin_type=True, use_single_float=False)


def _prepare(value: object, *, canonical: bool) -> Any:
    """Validate `value` and turn it into plain containers ready for the MessagePack packer."""
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, float):
        return _NAN if canonical and value != value else value
    if isinstance(value, int):
        _check_int(value)
        return value
    if isinstance(value, (list, tuple)):
        return [_prepare(item, canonical=canonical) for item in value]
    if isinstance(value, Mapping):
        items = [(escape_key(_check_key(k)), _prepare(v, canonical=canonical)) for k, v in value.items()]
        if canonical:
            items.sort(key=lambda item: item[0].encode("utf-8"))
        return dict(items)
    raise TypeError(f"not a wire value: {type(value).__name__}")


def _decode_map(pairs: list[tuple[object, Wire]]) -> FrozenMap:
    return FrozenMap({unescape_key(_decode_key(k)): v for k, v in pairs})


def _decode_key(key: object) -> str:
    if not isinstance(key, str):
        raise ValueError(f"map keys must be str, got {type(key).__name__}")
    return key


def _validate(root: object, max_depth: int) -> None:
    """Check nesting depth and value types without recursion."""
    stack: list[tuple[object, int]] = [(root, 0)]
    while stack:
        value, depth = stack.pop()
        if value is None or isinstance(value, (bool, float, str)):
            continue
        if isinstance(value, int):
            if not -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
                raise ValueError(f"integer out of range: {value}")
            continue
        if isinstance(value, tuple):
            children: Any = value
        elif isinstance(value, FrozenMap):
            children = value.values()
        else:
            raise ValueError(f"unsupported wire type: {type(value).__name__}")
        if depth + 1 > max_depth:
            raise ValueError(f"nesting depth exceeds {max_depth}")
        stack.extend((child, depth + 1) for child in children)
