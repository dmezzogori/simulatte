"""Tests for the private wire value model and canonical MessagePack encoding."""

from __future__ import annotations

import math
import struct
from typing import Any

import msgpack
import pytest

from simulatte._wire import (
    FrozenMap,
    canonical_pack,
    escape_key,
    freeze,
    new_packer,
    pack,
    prepared,
    prepared_op,
    unescape_key,
    unpack,
)


def fz(value: object) -> Any:
    """Freeze `value`, typed as `Any` so tests can index and measure the result."""
    return freeze(value)


def cpack(value: Any) -> bytes:
    """`canonical_pack` with an untyped argument, so tests can pass plain lists and dicts."""
    return canonical_pack(value)


def wpack(value: Any) -> bytes:
    """`pack` with an untyped argument, so tests can pass plain lists and dicts."""
    return pack(value)


def upk(data: bytes, **limits: int) -> Any:
    """`unpack` returning `Any`, so tests can index and measure the result."""
    return unpack(data, **limits)


def nested_depth(depth: int) -> Any:
    """Return `depth` nested single-element arrays around a scalar."""
    value: Any = 1
    for _ in range(depth):
        value = [value]
    return value


class TestFreeze:
    def test_converts_containers(self) -> None:
        v = fz({"b": [1, 2], "c": {"d": [3]}})

        assert isinstance(v, FrozenMap)
        assert v["b"] == (1, 2)
        assert isinstance(v["c"], FrozenMap)
        assert v["c"]["d"] == (3,)
        with pytest.raises(TypeError):
            v["c"] = 1

    def test_frozen_map_is_hashable_and_mapping_like(self) -> None:
        a = fz({"x": [1, {"y": 2}], "z": None})
        b = fz({"z": None, "x": (1, {"y": 2})})

        assert a == b
        assert hash(a) == hash(b)
        assert hash(a) == hash(a)
        assert a == {"x": (1, {"y": 2}), "z": None}
        assert len(a) == 2
        assert list(a) == ["x", "z"]
        assert "FrozenMap" in repr(a)
        assert {a: 1}[b] == 1

    @pytest.mark.parametrize("bad", [object(), {1: "x"}, {1, 2}, b"x", {"k": object()}, [b"x"]])
    def test_rejects_non_wire(self, bad: object) -> None:
        with pytest.raises(TypeError):
            freeze(bad)

    def test_int_range(self) -> None:
        assert freeze(2**53 - 1) == 2**53 - 1
        assert freeze(-(2**53 - 1)) == -(2**53 - 1)
        with pytest.raises(OverflowError):
            freeze(2**53)
        with pytest.raises(OverflowError):
            freeze(-(2**53))
        with pytest.raises(OverflowError):
            freeze({"k": [2**60]})

    def test_scalars_pass_through(self) -> None:
        for scalar in (None, True, False, 0, 1.5, "s", float("inf")):
            assert freeze(scalar) is scalar


class TestKeyEscaping:
    @pytest.mark.parametrize("key", ["__proto__", "constructor", "prototype", "~", "~x", "~__proto__", "plain", ""])
    def test_roundtrip(self, key: str) -> None:
        assert unescape_key(escape_key(key)) == key

    def test_escaped_forms(self) -> None:
        assert escape_key("__proto__") == "~__proto__"
        assert escape_key("constructor") == "~constructor"
        assert escape_key("prototype") == "~prototype"
        assert escape_key("~x") == "~~x"
        assert escape_key("~__proto__") == "~~__proto__"
        assert escape_key("plain") == "plain"
        assert unescape_key("plain") == "plain"


class TestCanonicalPack:
    def test_sorting_and_float64(self) -> None:
        assert cpack({"b": 1, "a": 2}) == cpack({"a": 2, "b": 1})
        assert cpack(1.0) == b"\xcb" + struct.pack(">d", 1.0)

    def test_sorted_by_escaped_utf8_bytes(self) -> None:
        # "~" (0x7e) sorts after "z"; "é" (0xc3 0xa9) sorts after both. The escaped "__proto__" is "~__proto__".
        data = cpack({"é": 1, "z": 2, "__proto__": 3, "a": 4})

        assert list(msgpack.unpackb(data, raw=False)) == ["a", "z", "~__proto__", "é"]

    def test_nested_maps_sorted_and_freeze_input(self) -> None:
        a = cpack({"o": {"y": 1, "x": [{"q": 1, "p": 2}]}})
        b = cpack(freeze({"o": {"x": [{"p": 2, "q": 1}], "y": 1}}))

        assert a == b

    def test_integers_use_smallest_form(self) -> None:
        assert cpack(5) == b"\x05"
        assert cpack(-1) == b"\xff"
        assert cpack(300) == b"\xcd\x01\x2c"
        assert cpack(2**53 - 1) == b"\xcf" + struct.pack(">Q", 2**53 - 1)

    def test_out_of_range_int_rejected(self) -> None:
        with pytest.raises(OverflowError):
            cpack(2**53)
        with pytest.raises(OverflowError):
            wpack([-(2**53)])

    def test_non_wire_rejected(self) -> None:
        with pytest.raises(TypeError):
            cpack(b"x")
        with pytest.raises(TypeError):
            cpack({1: 2})

    def test_tuples_and_lists_are_arrays(self) -> None:
        assert cpack((1, (2, 3))) == cpack([1, [2, 3]])

    def test_nan_normalized(self) -> None:
        assert cpack(float("nan")) == cpack(-float("nan"))
        assert cpack(float("nan")) == b"\xcb\x7f\xf8\x00\x00\x00\x00\x00\x00"
        assert cpack(("a", -float("nan"))) == cpack(("a", float("nan")))

    def test_bool_and_none(self) -> None:
        assert cpack((True, False, None)) == b"\x93\xc3\xc2\xc0"


class TestPackUnpack:
    def test_inf_roundtrip(self) -> None:
        assert upk(wpack(float("-inf"))) == float("-inf")
        assert upk(cpack(float("inf"))) == float("inf")

    def test_nan_roundtrip(self) -> None:
        assert math.isnan(upk(cpack(float("nan"))))

    def test_hostile_keys_roundtrip(self) -> None:
        v = {"__proto__": 1, "constructor": 2, "prototype": 3, "~x": 4, "~__proto__": 5, "n": {"constructor": 6}}

        assert upk(wpack(v)) == v
        assert upk(cpack(v)) == v

    def test_hostile_keys_are_escaped_on_wire(self) -> None:
        raw = msgpack.unpackb(wpack({"__proto__": 1, "~a": 2, "n": [{"constructor": 3}]}), raw=False)

        assert raw == {"~__proto__": 1, "~~a": 2, "n": [{"~constructor": 3}]}

    def test_roundtrip_result_is_frozen(self) -> None:
        out = upk(wpack({"a": [1, {"b": 2.5}], "c": "é", "d": None, "e": True}))

        assert isinstance(out, FrozenMap)
        assert out["a"] == (1, FrozenMap({"b": 2.5}))
        assert out == freeze({"a": [1, {"b": 2.5}], "c": "é", "d": None, "e": True})
        hash(out)

    def test_pack_is_non_canonical_but_equal_after_decode(self) -> None:
        assert wpack({"b": 1, "a": 2}) != cpack({"b": 1, "a": 2})
        assert upk(wpack({"b": 1, "a": 2})) == upk(cpack({"b": 1, "a": 2}))

    def test_depth_limit(self) -> None:
        assert upk(wpack(nested_depth(64))) is not None
        with pytest.raises(ValueError, match="depth"):
            upk(wpack(nested_depth(65)))
        with pytest.raises(ValueError, match="depth"):
            upk(wpack({"a": nested_depth(64)}))
        assert upk(wpack(nested_depth(5)), max_depth=5) is not None
        with pytest.raises(ValueError, match="depth"):
            upk(wpack(nested_depth(6)), max_depth=5)

    def test_collection_length_limit(self) -> None:
        assert upk(wpack(list(range(5))), max_len=5) == (0, 1, 2, 3, 4)
        with pytest.raises(ValueError):
            upk(wpack(list(range(6))), max_len=5)
        with pytest.raises(ValueError):
            upk(wpack({str(i): i for i in range(6)}), max_len=5)

    def test_malformed_input(self) -> None:
        with pytest.raises(ValueError):
            upk(b"\x92\x01")  # truncated array
        with pytest.raises(ValueError):
            upk(b"\x01\x02")  # trailing data

    def test_rejects_non_wire_values_on_the_wire(self) -> None:
        with pytest.raises(ValueError, match="bytes"):
            upk(msgpack.packb(b"x"))
        with pytest.raises(ValueError, match="bytes"):
            upk(msgpack.packb([{"a": b"x"}]))
        with pytest.raises(ValueError):
            upk(msgpack.packb(msgpack.ExtType(1, b"x")))
        with pytest.raises(ValueError):
            upk(msgpack.packb({b"k": 1}, use_bin_type=True))
        with pytest.raises(ValueError):
            upk(msgpack.packb({1: 1}))

    def test_rejects_out_of_range_integer(self) -> None:
        with pytest.raises(ValueError, match="range"):
            upk(msgpack.packb(2**53))
        assert upk(msgpack.packb(2**53 - 1)) == 2**53 - 1

    def test_accepts_float32_from_other_writers(self) -> None:
        assert upk(msgpack.packb(0.5, use_single_float=True)) == 0.5

    def test_rejects_duplicate_keys_after_unescaping(self) -> None:
        # Hand-built maps: the encoder never produces these, a hostile or broken writer might (R4).
        same = b"\x82\xa1a\x01\xa1a\x02"  # {"a": 1, "a": 2}
        with pytest.raises(ValueError, match="duplicate map key"):
            upk(same)
        collide = b"\x82\xa1a\x01\xa2~a\x02"  # "~a" unescapes to "a"
        with pytest.raises(ValueError, match="duplicate map key"):
            upk(collide)
        nested = msgpack.packb([{"x": {"~__proto__": 1, "~~__proto__": 2}}])  # distinct after unescaping
        assert upk(nested) == ({"x": {"__proto__": 1, "~__proto__": 2}},)
        hostile = b"\x91\x82\xaa~prototype\x01\xa9prototype\x02"  # "~prototype" and a raw "prototype"
        with pytest.raises(ValueError, match="duplicate map key"):
            upk(hostile)


class TestCanonicalByConstruction:
    """freeze() builds canonical values, which the fast packers emit without per-item preparation."""

    def test_freeze_sorts_map_keys_by_escaped_utf8_bytes(self) -> None:
        v = fz({"é": 1, "z": 2, "__proto__": 3, "a": 4, "~b": 5})

        assert list(v) == ["a", "z", "__proto__", "~b", "é"]  # escaped: a, z, ~__proto__, ~~b, é
        assert v._wire == {"a": 4, "z": 2, "~__proto__": 3, "~~b": 5, "é": 1}
        assert freeze(v) is v  # a canonical map is returned unchanged

    def test_freeze_normalizes_nan_and_keeps_signed_zero(self) -> None:
        negative_nan = math.copysign(float("nan"), -1.0)
        v = fz({"n": negative_nan, "z": -0.0})

        assert struct.pack(">d", v["n"]) == b"\x7f\xf8\x00\x00\x00\x00\x00\x00"
        assert struct.pack(">d", v["z"]) == struct.pack(">d", -0.0)

    def test_constructor_maps_are_not_canonical(self) -> None:
        direct = FrozenMap({"b": 1, "a": 2})

        assert direct._wire is None and list(direct) == ["b", "a"]
        assert freeze(direct) == direct and list(fz(direct)) == ["a", "b"]
        assert cpack(direct) == cpack(fz(direct))

    @pytest.mark.parametrize(
        "value",
        [
            None,
            True,
            0,
            -33,
            2**53 - 1,
            1.5,
            -0.0,
            float("inf"),
            float("nan"),
            math.copysign(float("nan"), -1.0),
            "é",
            (1, "a", (2.5, None)),
            [1, {"b": 1, "a": 2}],
            {"z": {"constructor": [float("nan")]}, "a": FrozenMap({"y": 1, "x": 2})},
            fz({"z": {"~": [1, {"q": -0.0}]}, "__proto__": None}),
        ],
    )
    def test_fast_packer_matches_canonical_pack(self, value: Any) -> None:
        packer = new_packer()

        assert packer.pack(prepared(value)) == cpack(value)
        op = ("set", "e", "f", value)
        assert packer.pack(prepared_op(op)) == cpack(op)

    def test_fast_path_leaves_ready_values_unprepared(self) -> None:
        frozen = fz({"b": [1, 2], "a": None})
        op = ("put", "e", "f", "k", frozen)

        assert prepared(frozen) is frozen
        assert prepared_op(op) is op
        assert prepared_op(("set", "e", "f", (1, 2))) == ["set", "e", "f", [1, 2]]  # tuples go the full way

    def test_fast_path_rejects_like_canonical_pack(self) -> None:
        with pytest.raises(OverflowError):
            prepared(2**53)
        with pytest.raises(OverflowError):
            prepared_op(("set", "e", "f", -(2**53)))
        with pytest.raises(TypeError):
            prepared(b"x")
        with pytest.raises(TypeError, match="not a prepared wire value"):
            new_packer().pack(FrozenMap({"a": 1}))  # only canonical maps may reach the packer unprepared

    def test_without_keeps_canonical_maps_canonical(self) -> None:
        canonical = fz({"c": 3, "~a": 1, "b": 2})
        direct = FrozenMap({"c": 3, "a": 1})

        smaller = canonical.without(frozenset({"b"}))
        assert smaller == {"c": 3, "~a": 1} and smaller._wire == {"c": 3, "~~a": 1}
        assert direct.without(frozenset({"a"})) == {"c": 3} and direct.without(frozenset())._wire is None
