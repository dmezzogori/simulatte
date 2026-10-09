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

Values made by :func:`freeze` are canonical by construction: NaN is normalized and every :class:`FrozenMap` it
builds keeps its keys sorted by the UTF-8 bytes of the escaped key and holds the escaped form ready for the
packer. Encoding such values skips the per-item preparation; :func:`prepared` and :func:`prepared_op` give the
hot paths (digest, recorder) packable values and fall back to the full preparation for anything else.
"""

from __future__ import annotations

from bisect import bisect_left
from collections import Counter
from collections.abc import Iterable, Iterator, Mapping
from typing import Any, TypeAlias

import msgpack

__all__ = [
    "MAX_SAFE_INT",
    "FrozenMap",
    "Wire",
    "canonical_pack",
    "escape_key",
    "freeze",
    "new_packer",
    "pack",
    "prepared",
    "prepared_op",
    "unescape_key",
    "unpack",
]

MAX_SAFE_INT = 2**53 - 1

_HOSTILE_KEYS = frozenset({"__proto__", "constructor", "prototype"})
_NAN = float("nan")

Wire: TypeAlias = "None | bool | int | float | str | tuple[Wire, ...] | FrozenMap"


class FrozenMap(Mapping[str, "Wire"]):
    """Immutable, hashable mapping from ``str`` to wire values.

    The constructor keeps the insertion order and does not validate the values. A map built by :func:`freeze` is
    *canonical*: keys sorted by the UTF-8 bytes of the escaped key and values frozen, so the packers emit it
    as is.
    """

    __slots__ = ("_data", "_hash", "_wire")

    def __init__(self, data: Mapping[str, Wire]) -> None:
        self._data: dict[str, Wire] = dict(data)
        self._hash: int | None = None
        self._wire: dict[str, Wire] | None = None  # canonical maps only: escaped keys, ready for the packer

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

    def without(self, keys: frozenset[str]) -> FrozenMap:
        """This map without `keys`; the result is canonical when this map is."""
        if self._wire is None:
            return FrozenMap({k: v for k, v in self._data.items() if k not in keys})
        return _canonical_sorted({k: v for k, v in self._data.items() if k not in keys})


def freeze(value: Any) -> Wire:
    """Return `value` as an immutable wire value, canonical by construction.

    Lists and tuples become tuples, mappings become canonical :class:`FrozenMap` (keys sorted by the UTF-8 bytes
    of the escaped key), NaN becomes the normalized NaN; a canonical map is returned unchanged. Raises
    `TypeError` for anything that is not a wire value (including non-``str`` keys and ``bytes``) and
    `OverflowError` for integers outside +/-(2**53 - 1).
    """
    t = type(value)
    if t is str or value is None or t is bool:  # exact types first: the common, cheapest checks
        return value
    if t is float:
        return value if value == value else _NAN
    if t is int:
        _check_int(value)
        return value
    if isinstance(value, (bool, str)):
        return value
    if isinstance(value, float):
        return _NAN if value != value else value
    if isinstance(value, int):
        _check_int(value)
        return value
    if isinstance(value, FrozenMap) and value._wire is not None:
        return value
    if isinstance(value, (list, tuple)):
        return tuple(freeze(item) for item in value)
    if isinstance(value, Mapping):
        return _canonical_map({_check_key(k): freeze(v) for k, v in value.items()})
    raise TypeError(f"not a wire value: {type(value).__name__}")


def _canonical_map(data: dict[str, Wire]) -> FrozenMap:
    """A canonical :class:`FrozenMap` of `data`, whose keys are ``str`` and values frozen."""
    keys = sorted(data)
    tilde = bisect_left(keys, "~")  # keys starting with "~" are contiguous in sorted order
    if (tilde < len(keys) and keys[tilde].startswith("~")) or not _HOSTILE_KEYS.isdisjoint(keys):
        # Code point order equals UTF-8 byte order, so sorting the escaped strings sorts by their UTF-8 bytes.
        keys.sort(key=escape_key)
        result = _canonical_sorted({k: data[k] for k in keys})
    else:  # no key is escaped, so the plain order is the canonical one
        result = FrozenMap.__new__(FrozenMap)
        result._data = result._wire = {k: data[k] for k in keys}
        result._hash = None
    return result


def _canonical_sorted(ordered: dict[str, Wire]) -> FrozenMap:
    """A canonical :class:`FrozenMap` of `ordered`, already in canonical key order with frozen values."""
    result = FrozenMap.__new__(FrozenMap)
    result._data = ordered
    result._hash = None
    if any(k in _HOSTILE_KEYS or k.startswith("~") for k in ordered):
        result._wire = {escape_key(k): v for k, v in ordered.items()}
    else:
        result._wire = ordered
    return result


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


def prepared(value: Any) -> Any:
    """`value` ready for a packer from :func:`new_packer`, which then emits its canonical encoding.

    Scalars and canonical maps are returned as they are (NaN normalized); anything else goes through the full
    preparation, which raises like :func:`canonical_pack` for values outside the wire model.
    """
    t = type(value)
    if t is str or t is bool or value is None:
        return value
    if t is int:
        if -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
            return value
    elif t is float:
        return _NAN if value != value else value
    elif t is FrozenMap and value._wire is not None:
        return value
    return _prepare(value, canonical=True)


def prepared_op(op: tuple[Any, ...]) -> Any:
    """A delta operation ready for a packer from :func:`new_packer` (see :func:`prepared`).

    Operations made of strings, safe integers, non-NaN floats, booleans, None and canonical maps are returned
    unchanged; any other item sends the whole operation through the full preparation.
    """
    for item in op:
        t = type(item)
        if t is str or item is None or t is bool:
            continue
        if t is int:
            if -MAX_SAFE_INT <= item <= MAX_SAFE_INT:
                continue
        elif t is float:
            if item == item:
                continue
        elif t is FrozenMap and item._wire is not None:
            continue
        return _prepare(op, canonical=True)
    return op


def new_packer() -> msgpack.Packer:
    """A MessagePack packer for values from :func:`prepared` and :func:`prepared_op` (one per thread)."""
    return msgpack.Packer(use_bin_type=True, use_single_float=False, default=_pack_default, autoreset=True)


def pack(value: Wire) -> bytes:
    """Encode `value` to MessagePack with escaped keys, without sorting."""
    return _packb(_prepare(value, canonical=False))


def unpack(data: bytes, *, max_depth: int = 64, max_len: int = 10**7) -> Wire:
    """Decode MessagePack `data` into a wire value, enforcing limits.

    `max_depth` bounds container nesting and `max_len` bounds the length of any array or map. Raises
    `ValueError` for malformed or truncated input, trailing data, limit violations, values outside the
    wire model (``bytes``, extension types, non-``str`` keys, integers beyond +/-(2**53 - 1)) and maps
    whose keys repeat after unescaping (for example ``"a"`` and ``"~a"``).
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
    return msgpack.packb(obj, use_bin_type=True, use_single_float=False, default=_pack_default)


def _pack_default(obj: object) -> Any:
    """Packer hook: a canonical :class:`FrozenMap` packs as its escaped, sorted form."""
    if isinstance(obj, FrozenMap) and obj._wire is not None:
        return obj._wire
    raise TypeError(f"not a prepared wire value: {type(obj).__name__}")


def _prepare(value: object, *, canonical: bool) -> Any:
    """Validate `value` and turn it into plain containers ready for the MessagePack packer.

    Canonical maps are already validated and sorted; they are left for the packer hook.
    """
    if value is None or isinstance(value, (bool, str)):
        return value
    if type(value) is FrozenMap and value._wire is not None:
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


def _decode_map(pairs: Iterable[tuple[object, Wire]]) -> FrozenMap:
    pairs = list(pairs)  # msgpack's pure-Python fallback (used on PyPy) passes a generator
    data = {unescape_key(_decode_key(k)): v for k, v in pairs}
    if len(data) != len(pairs):  # two encoded keys decode to the same key: the map would be ambiguous
        counts = Counter(unescape_key(_decode_key(k)) for k, _ in pairs)
        duplicate = next(key for key, n in counts.items() if n > 1)
        raise ValueError(f"duplicate map key after unescaping: {duplicate!r}")
    return FrozenMap(data)


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
