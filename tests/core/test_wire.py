"""Tests for the private wire value model and canonical MessagePack encoding."""

from __future__ import annotations

import math
import struct
from typing import Any, ClassVar

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
    wire_float,
    wire_equal,
    wire_float_or_none,
    wire_time,
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


def test_freeze_accepts_scalar_subclasses_like_before() -> None:
    """The exact-type fast path of freeze() leaves subclasses (enums, numpy-like floats) to the general checks."""
    import enum

    class Color(enum.StrEnum):
        RED = "red"

    class Level(enum.IntEnum):
        HIGH = 3

    class Ratio(float):
        pass

    assert freeze(Color.RED) == "red" and type(freeze(Color.RED)) is str
    assert freeze(Level.HIGH) == 3 and type(freeze(Level.HIGH)) is int
    assert freeze(Ratio(0.5)) == 0.5 and type(freeze(Ratio(0.5))) is float and math.isnan(fz(Ratio("nan")))
    with pytest.raises(OverflowError):
        freeze(type("Big", (int,), {})(2**60))
    assert cpack(fz({"c": Color.RED, "l": Level.HIGH})) == cpack({"c": "red", "l": 3})


class _Spy:
    """Collects the user-level methods that the wire functions called (they must call none, spec §6.1)."""

    calls: ClassVar[list[str]] = []


class _SpyFloat(float):
    def __float__(self) -> float:
        _Spy.calls.append("float.__float__")
        return 7.0

    def __ne__(self, other: object) -> bool:
        _Spy.calls.append("float.__ne__")
        return True

    def __eq__(self, other: object) -> bool:
        _Spy.calls.append("float.__eq__")
        return False

    __hash__ = float.__hash__


class _SpyInt(int):
    def __int__(self) -> int:
        _Spy.calls.append("int.__int__")
        return 7

    def __index__(self) -> int:
        _Spy.calls.append("int.__index__")
        return 7

    def __float__(self) -> float:
        _Spy.calls.append("int.__float__")
        return 7.0

    def __le__(self, other: object) -> bool:
        _Spy.calls.append("int.__le__")
        return True

    def __ge__(self, other: object) -> bool:
        _Spy.calls.append("int.__ge__")
        return True

    __hash__ = int.__hash__


class _SpyStr(str):
    def __str__(self) -> str:
        _Spy.calls.append("str.__str__")
        return "spy"

    def __lt__(self, other: object) -> bool:
        _Spy.calls.append("str.__lt__")
        return False

    def encode(self, *args: Any, **kwargs: Any) -> bytes:
        _Spy.calls.append("str.encode")
        return b"spy"

    __hash__ = str.__hash__


class _SpyList(list):  # type: ignore[type-arg]
    def __iter__(self) -> Any:
        _Spy.calls.append("list.__iter__")
        return iter([])


class _SpyDict(dict):  # type: ignore[type-arg]
    def items(self) -> Any:
        _Spy.calls.append("dict.items")
        return iter([])

    def __iter__(self) -> Any:
        _Spy.calls.append("dict.__iter__")
        return iter([])


class TestNoUserCode:
    """Wire conversions read subclasses of built-in types through the built-in methods (spec §6.1)."""

    @pytest.fixture(autouse=True)
    def _reset(self) -> None:
        _Spy.calls.clear()

    def _value(self) -> Any:
        return _SpyDict(
            {
                _SpyStr("b"): _SpyList([_SpyFloat(1.5), _SpyInt(2), _SpyStr("s"), _SpyFloat("nan")]),
                _SpyStr("a"): (_SpyInt(3), _SpyFloat(-0.0)),
            }
        )

    def test_freeze_returns_built_in_types_without_calling_user_code(self) -> None:
        frozen = fz(self._value())

        assert _Spy.calls == []
        assert list(frozen) == ["a", "b"] and all(type(k) is str for k in frozen)
        a, b = frozen["a"], frozen["b"]
        assert [type(x) for x in (*a, *b)] == [int, float, float, int, str, float]
        assert a[0] == 3 and math.copysign(1.0, a[1]) == -1.0
        assert b[:3] == (1.5, 2, "s") and math.isnan(b[3])

    def test_freeze_checks_the_integer_range_without_user_code(self) -> None:
        with pytest.raises(OverflowError):
            freeze(_SpyInt(2**60))
        assert _Spy.calls == []

    @pytest.mark.parametrize("encode", [canonical_pack, pack], ids=["canonical_pack", "pack"])
    def test_encoders_call_no_user_code(self, encode: Any) -> None:
        data = encode(self._value())

        assert _Spy.calls == []
        assert cpack(upk(data)) == cpack(fz(self._value()))  # bytes: NaN != NaN as values

    def test_fallback_packer_calls_no_user_code(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from msgpack import fallback

        monkeypatch.setattr(msgpack, "Packer", fallback.Packer)  # the pure-Python packer that PyPy uses
        data = canonical_pack(self._value())

        assert _Spy.calls == []
        assert cpack(upk(data)) == cpack(fz(self._value()))  # bytes: NaN != NaN as values

    def test_prepared_values_call_no_user_code(self) -> None:
        packer = new_packer()
        packer.pack(prepared(_SpyFloat(2.5)))
        packer.pack(prepared_op(("set", _SpyStr("e"), "f", _SpyInt(1))))
        assert _Spy.calls == []

    def test_mappings_other_than_dict_and_frozen_map_are_not_wire_values(self) -> None:
        from collections.abc import Mapping
        from types import MappingProxyType

        class Custom(Mapping[str, int]):
            def __getitem__(self, key: str) -> int:  # pragma: no cover - must never be called
                raise AssertionError("user code")

            def __iter__(self) -> Any:  # pragma: no cover - must never be called
                raise AssertionError("user code")

            def __len__(self) -> int:  # pragma: no cover - must never be called
                raise AssertionError("user code")

        # A proxy may wrap a user mapping, whose methods reading it would call (ruling R34): not a wire value either.
        for value in (Custom(), MappingProxyType({"k": 1}), MappingProxyType(Custom())):
            with pytest.raises(TypeError, match="not a wire value"):
                freeze(value)
            with pytest.raises(TypeError, match="not a wire value"):
                canonical_pack(value)  # ty: ignore[invalid-argument-type]


class TestWireFloat:
    def test_builtin_numbers_convert(self) -> None:
        assert wire_float(1.5) == 1.5 and type(wire_float(3)) is float and wire_float(True) == 1.0
        assert math.isnan(wire_float(10**400)) and wire_float_or_none(10**400) is None  # beyond the float range

    def test_subclasses_convert_through_the_builtin_methods(self) -> None:
        values = [wire_float(_SpyFloat(2.5)), wire_float(_SpyInt(4)), wire_float_or_none(_SpyFloat(0.5))]

        assert values == [2.5, 4.0, 0.5] and [type(v) for v in values] == [float, float, float]
        assert _Spy.calls == []

    def test_stdlib_numbers_convert(self) -> None:
        """Ruling R32: Fraction and Decimal are framework-safe and convert with float()."""
        from decimal import Decimal
        from fractions import Fraction

        assert wire_float(Fraction(1, 2)) == 0.5 and wire_float(Decimal("2.5")) == 2.5
        assert type(wire_float(Fraction(3, 1))) is float and math.isnan(wire_float(Decimal("NaN")))
        assert wire_float_or_none(Decimal("sNaN")) is None  # float() refuses a signalling NaN

    def test_other_conversions_are_never_called(self) -> None:
        from decimal import Decimal
        from fractions import Fraction

        class Number:
            def __float__(self) -> float:  # pragma: no cover - must never be called
                raise AssertionError("user code")

        class MyFraction(Fraction):
            def __float__(self) -> float:  # pragma: no cover - must never be called
                raise AssertionError("user code")

        class MyDecimal(Decimal):
            def __float__(self) -> float:  # pragma: no cover - must never be called
                raise AssertionError("user code")

        for value in (Number(), MyFraction(1, 2), MyDecimal("0.5"), "1.5", None, (1.0,)):
            assert wire_float_or_none(value) is None and math.isnan(wire_float(value))

    def test_numpy_scalars_convert_and_nothing_else_from_numpy(self) -> None:
        """Ruling R34: NumPy scalar numbers (numpy.number, numpy.bool_) convert; object arrays and other arrays, and
        subclasses that define their own ``__float__``, are not numbers for events. numpy is installed with
        gymnasium, so this runs on CPython and PyPy alike."""
        np = pytest.importorskip("numpy")

        class Number:
            def __float__(self) -> float:  # pragma: no cover - must never be called
                raise AssertionError("user code")

        class Shadow(np.float32):
            def __float__(self) -> float:  # pragma: no cover - must never be called
                raise AssertionError("user code")

        class Plain(np.int64):
            pass

        assert wire_float(np.int64(3)) == 3.0 and type(wire_float(np.int64(3))) is float
        assert wire_float(np.float32(0.5)) == 0.5 and wire_float(np.float64(1.25)) == 1.25
        assert wire_float(np.bool_(True)) == 1.0 and wire_float(Plain(4)) == 4.0
        for value in (np.array(Number(), dtype=object), np.array(1.5), np.array([1.0]), Shadow(0.5)):
            assert wire_float_or_none(value) is None

    def test_numpy_times_and_complex_numbers_are_not_numbers_for_events(self) -> None:
        """Ruling R34 (fix wave 3): timedelta64 (a numpy.signedinteger), NaT, datetime64 and complex scalars are not
        numbers for events. float() would raise or drop the imaginary part only when observed; they are NaN or None,
        wire_time raises its documented TypeError and a priority is null."""
        import warnings

        np = pytest.importorskip("numpy")
        from simulatte.server import _wire_priority

        with warnings.catch_warnings():
            warnings.simplefilter("error")  # a ComplexWarning would mean float() ran on a complex value
            for value in (
                np.timedelta64(5, "s"),
                np.timedelta64("NaT", "s"),
                np.datetime64("2026-01-01"),
                np.complex128(1 + 2j),
                np.complex64(1),
            ):
                assert wire_float_or_none(value) is None and math.isnan(wire_float(value)), value
                assert _wire_priority(value) is None, value
                with pytest.raises(TypeError, match="numeric simulation time"):
                    wire_time(value)


class TestWireEqual:
    """Replay value equality is canonical-encoding equality (ruling R31, spec §6.2, §9.1)."""

    NAN = float("nan")
    VALUES: ClassVar[list[Any]] = [
        None, True, False, 0, 1, -1, 0.0, -0.0, 1.0, 1.5, NAN, -NAN, float("inf"), "", "1", "a",
        (), (1,), (1.0,), (True,), (NAN,), (1, (NAN, -0.0)), [1, 2], (1, 2),
        FrozenMap({}), FrozenMap({"x": 1}), FrozenMap({"x": 1.0}), FrozenMap({"x": NAN}), {"x": 1}, {"y": 1},
        FrozenMap({"x": 1, "y": (NAN,)}),
    ]  # fmt: skip

    def test_matches_canonical_encoding_equality(self) -> None:
        for a in self.VALUES:
            for b in self.VALUES:
                assert wire_equal(a, b) is (canonical_pack(a) == canonical_pack(b)), (a, b)

    def test_cases_of_the_ruling(self) -> None:
        assert not wire_equal(True, 1) and not wire_equal(1, True) and not wire_equal(False, 0)
        assert not wire_equal(1, 1.0) and not wire_equal(1.0, 1)
        assert wire_equal(float("nan"), float("nan")) and wire_equal((1, float("nan")), (1, float("nan")))
        assert not wire_equal(-0.0, 0.0) and wire_equal(-0.0, -0.0)
        assert wire_equal((1, 2), [1, 2]) and wire_equal({"k": (1,)}, FrozenMap({"k": [1]}))  # ty: ignore[invalid-argument-type]

    def test_subclasses_compare_like_their_built_in_values(self) -> None:
        import enum

        class Level(enum.IntEnum):
            HIGH = 3

        class Color(enum.StrEnum):
            RED = "red"

        assert wire_equal(Level.HIGH, 3) and wire_equal("red", Color.RED) and not wire_equal(Level.HIGH, 3.0)
        assert wire_equal(_SpyFloat(1.5), 1.5) and _Spy.calls == []

    def test_rejects_values_outside_the_wire_model(self) -> None:
        for a, b in (((1,), {1, 2}), (object(), (1,))):
            with pytest.raises(TypeError, match="not a wire value"):
                wire_equal(a, b)


@pytest.mark.parametrize("n", [197, 198, 200, 1000])
def test_leading_byte_order_mark_is_kept_at_every_length(n: int) -> None:
    """Strings keep a leading U+FEFF, short or long, as keys or values (mirrored in
    ``studio/packages/trace/test/hostile.test.ts``: the TS library used to drop it above 200 bytes)."""
    text = "\ufeff" + "x" * n
    assert upk(wpack([text, "x" * n]))[0] == text
    assert len(upk(wpack({text: 1, "x" * n: 2}))) == 2


class TestPureFallbackDecoder:
    """msgpack's pure-Python fallback (the one PyPy uses) hands ``object_pairs_hook`` a generator, not a list."""

    @pytest.fixture(autouse=True)
    def _fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from msgpack import fallback

        monkeypatch.setattr(msgpack, "unpackb", fallback.unpackb)

    def test_maps_decode_from_a_generator_of_pairs(self) -> None:
        v = {"b": (1, {"y": 2.5, "__proto__": None}), "~a": "é", "c": {}}

        assert upk(wpack(v)) == v
        assert upk(cpack(fz(v))) == v

    def test_duplicate_keys_still_rejected(self) -> None:
        raw = msgpack.packb({"a": 1, "~a": 2})
        with pytest.raises(ValueError, match="duplicate map key"):
            unpack(raw)
