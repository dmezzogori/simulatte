"""Model metadata must preserve plain calls and reject ambiguous parameter schemas."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass, field, make_dataclass
from enum import Enum
import importlib
import math
from typing import Annotated, Literal

import pytest

import simulatte


class Policy(Enum):
    FAST = "fast"
    SLOW = "slow"


@dataclass(frozen=True)
class Params:
    amount: int
    rate: Annotated[float, "range-placeholder"] = 1.0


def api():
    assert callable(getattr(simulatte, "model", None)), "model decorator is missing"
    return importlib.import_module("simulatte.model")


def test_decorator_preserves_identity_and_direct_call():
    m = api()

    @dataclass(frozen=True)
    class P:
        count: int = 3

    def build(env, layout, params):
        return params.count

    decorated = m.model(params=P, horizon=10)(build)
    assert decorated is build
    assert build(None, None, P()) == 3
    spec = m.get_model_spec(build)
    assert spec.params is P
    with pytest.raises(FrozenInstanceError):
        spec.horizon = 4
    assert m.parse_parameters(m.parameter_schema(spec), {}) == {"count": 3}


def test_typed_cli_values_and_defaults():
    m = api()
    P = make_dataclass(
        "P",
        [
            ("required", str),
            ("flag", bool, False),
            ("count", int, 3),
            ("rate", Annotated[float, m.Range(0, 2)], 1.0),
            ("policy", Policy, Policy.FAST),
            ("kind", Literal["a", "b"], "a"),
            ("label", Annotated[str, m.Length(1, 4), m.Description("Name")], "abc"),
        ],
        frozen=True,
    )
    spec = m.get_model_spec(m.model(params=P, horizon=5)(lambda env, layout, params: None))
    schema = m.parameter_schema(spec)
    values = m.parse_parameters(schema, {"required": "日本語", "flag": "true", "rate": "1", "policy": "SLOW"})
    assert values == {
        "required": "日本語",
        "flag": True,
        "count": 3,
        "rate": 1.0,
        "policy": "SLOW",
        "kind": "a",
        "label": "abc",
    }
    assert type(values["rate"]) is float
    params = m.construct_parameters(spec, values)
    assert params.policy is Policy.SLOW
    data = schema.to_dict()
    restored = m.ParameterSchema.from_dict(data)
    assert m.validate_parameters(restored, values) == values
    assert data["fields"][-1]["description"] == "Name"


@pytest.mark.parametrize(
    "values",
    [
        {"flag": "1"},
        {"count": "1.5"},
        {"rate": "nan"},
        {"rate": "inf"},
        {"rate": "2.1"},
        {"policy": "fast"},
        {"kind": "z"},
        {"label": ""},
        {"oops": "a"},
    ],
)
def test_invalid_cli_values(values):
    m = api()
    P = make_dataclass(
        "P",
        [
            ("flag", bool, False),
            ("count", int, 1),
            ("rate", Annotated[float, m.Range(0, 2)], 1.0),
            ("policy", Policy, Policy.FAST),
            ("kind", Literal["a", "b"], "a"),
            ("label", Annotated[str, m.Length(1, 4)], "abc"),
        ],
        frozen=True,
    )
    schema = m.parameter_schema(m.get_model_spec(m.model(params=P, horizon=5)(lambda env, layout, params: None)))
    with pytest.raises(ValueError):
        m.parse_parameters(schema, values)


def test_required_duplicates_bool_int_bounds_and_signed_zero():
    m = api()
    P = make_dataclass("P", [("n", int), ("x", float, 0.0)], frozen=True)
    schema = m.parameter_schema(m.get_model_spec(m.model(params=P, horizon=5)(lambda env, layout, params: None)))
    for raw in [{}, {"n": str(2**53)}, [("n", "1"), ("n", "2")]]:
        with pytest.raises(ValueError):
            m.parse_parameters(schema, raw)
    for value in [True, 2**53, 1.0]:
        with pytest.raises(ValueError):
            m.validate_parameters(schema, {"n": value})
    assert m.parse_parameters(schema, {"n": str(-(2**53 - 1))})["n"] == -(2**53 - 1)
    result = m.validate_parameters(schema, {"n": 0, "x": -0.0})
    assert math.copysign(1, result["x"]) == 1


@pytest.mark.parametrize(
    "annotation,default",
    [
        (list[int], []),
        (int | None, None),
        (Annotated[int, "unknown"], 1),
        (float, float("nan")),
        (Literal[1, True], 1),
        (str, 1),
    ],
)
def test_unsupported_parameter_schema(annotation, default):
    m = api()
    P = make_dataclass("P", [("x", annotation, field(default_factory=lambda: default))], frozen=True)
    with pytest.raises(ValueError):
        m.model(params=P, horizon=5)(lambda env, layout, params: None)
    if not isinstance(default, list):
        P = make_dataclass("P", [("x", annotation, default)], frozen=True)
        with pytest.raises(ValueError):
            m.model(params=P, horizon=5)(lambda env, layout, params: None)


def test_invalid_metadata_and_constraints():
    m = api()
    P = make_dataclass("P", [("x", int, 1)])
    with pytest.raises(ValueError):
        m.model(params=P, horizon=5)(lambda env, layout, params: None)
    P = make_dataclass("P", [("x", int, 1)], frozen=True)
    for kwargs in [
        {},
        {"horizon": 0},
        {"horizon": True},
        {"horizon": 1, "warmup": 1},
        {"horizon": 1, "inputs": ["../secret"]},
        {"horizon": 1, "inputs": ["x", "x"]},
    ]:
        with pytest.raises(ValueError):
            m.model(params=P, **kwargs)(lambda env, layout, params: None)
    for create in [
        lambda: m.Range(2, 1),
        lambda: m.Range(float("nan")),
        lambda: m.Length(-1),
        lambda: m.Length(2, 1),
        lambda: m.Description(123),
    ]:
        with pytest.raises(ValueError):
            create()
    for annotation in [
        Annotated[str, m.Range(0, 1)],
        Annotated[int, m.Length(0, 2)],
        Annotated[int, m.Range(0, 2), m.Range(0, 3)],
    ]:
        Q = make_dataclass("Q", [("x", annotation)], frozen=True)
        with pytest.raises(ValueError):
            m.model(params=Q, horizon=5)(lambda env, layout, params: None)


def test_cross_field_constructor_validation_is_not_skipped():
    m = api()

    @dataclass(frozen=True)
    class P:
        n: int = 1

        def __post_init__(self):
            if self.n == 2:
                raise ValueError("two forbidden")

    spec = m.get_model_spec(m.model(params=P, horizon=5)(lambda env, layout, params: None))
    with pytest.raises(ValueError, match="two forbidden"):
        m.construct_parameters(spec, {"n": 2})


def test_float_bounds_use_float64_not_safe_integer_limit():
    m = api()
    P = make_dataclass("P", [("x", Annotated[float, m.Range(0, 1e100)], 1e99)], frozen=True)
    spec = m.get_model_spec(m.model(params=P, horizon=1e100)(lambda e, layout, p: None))
    assert m.parse_parameters(spec.schema, {}) == {"x": 1e99}


def test_schema_rejects_malformed_data_and_enum_aliases():
    m = api()

    class Aliased(Enum):
        A = 1
        B = 1

    for annotation in (Aliased, Literal[None]):
        P = make_dataclass("P", [("x", annotation)], frozen=True)
        with pytest.raises(ValueError):
            m.model(params=P, horizon=1)(lambda e, layout, p: None)
    field_data = {
        "name": "n",
        "type": "int",
        "required": False,
        "default": 1,
        "choices": [],
        "range": None,
        "length": None,
        "description": "",
    }
    for data in [
        {"version": True, "fields": []},
        {"version": 1, "fields": "oops"},
        {"version": 1, "fields": [field_data | {"extra": 1}]},
        {"version": 1, "fields": [field_data | {"choices": [1, 1]}]},
        {"version": 1, "fields": [field_data | {"required": True}]},
        {"version": 1, "fields": [field_data, field_data]},
        {"version": 1, "fields": [field_data | {"range": {"wat": 0}}]},
        {"version": 1, "fields": [field_data | {"choices": None}]},
    ]:
        with pytest.raises(ValueError):
            m.ParameterSchema.from_dict(data)


def test_metadata_rejects_invalid_declarations_and_accepts_typed_stops():
    m = api()
    P = make_dataclass("P", [("x", int, 1)], frozen=True)
    for kwargs in [
        {"horizon": 1, "warmup": -1},
        {"horizon": 1, "time_unit": ""},
        {"horizon": 1, "inputs": "data.csv"},
        {"stop": object()},
        {"stop": simulatte.Horizon(2), "warmup": 1},
    ]:
        with pytest.raises(ValueError):
            m.model(params=P, **kwargs)(lambda e, layout, p: None)
    for stop in [simulatte.Horizon(2), simulatte.FinitePopulation(arrivals=1, safety_limit=3)]:
        build = m.model(params=P, stop=stop)(lambda e, layout, p: None)
        assert m.get_model_spec(build).stop == stop
        with pytest.raises(ValueError):
            m.model(params=P, horizon=3)(build)
    with pytest.raises(ValueError):
        m.get_model_spec(lambda: None)

    class BadEnum(Enum):
        VALUE = []

    Q = make_dataclass("Q", [("x", BadEnum)], frozen=True)
    with pytest.raises(ValueError):
        m.model(params=Q, horizon=1)(lambda: None)
    Q = make_dataclass("Q", [("x", Policy, "wrong")], frozen=True)
    with pytest.raises(ValueError):
        m.model(params=Q, horizon=1)(lambda: None)
    Q = make_dataclass("Q", [("x", "UnknownType")], frozen=True)
    with pytest.raises(ValueError):
        m.model(params=Q, horizon=1)(lambda: None)
    schema = m.parameter_schema(m.get_model_spec(m.model(params=P, horizon=1)(lambda: None)))
    with pytest.raises(ValueError):
        m.validate_parameters(schema, {"unknown": 1})
    with pytest.raises(ValueError):
        m.parse_parameters(schema, {"x": 1})
    Q = make_dataclass("Q", [("x", float, 1.0)], frozen=True)
    schema = m.parameter_schema(m.get_model_spec(m.model(params=Q, horizon=1)(lambda: None)))
    with pytest.raises(ValueError):
        m.parse_parameters(schema, {"x": "1_000"})
    with pytest.raises(ValueError):
        m.parse_parameters(schema, {"x": "nonsense"})
    Q = make_dataclass("Q", [("x", str, "x")], frozen=True)
    schema = m.parameter_schema(m.get_model_spec(m.model(params=Q, horizon=1)(lambda: None)))
    with pytest.raises(ValueError):
        m.validate_parameters(schema, {"x": "a" * 65537})


def test_measurement_plans_are_validated_and_recorded():
    from simulatte.analysis import DatasetSchema, Field, MeasurementPlan

    m = api()

    @dataclass(frozen=True)
    class P:
        count: int = 3

    plan = MeasurementPlan("jobs", (DatasetSchema("jobs", (Field("x", "int64"),)),))
    build = m.model(params=P, horizon=1, measurement_plans=(plan,))(lambda e, layout, p: None)
    assert m.get_model_spec(build).measurement_plans == (plan,)
    for plans in ([plan], ("jobs",), (plan, plan)):
        with pytest.raises(ValueError, match="measurement plan"):
            m.model(params=P, horizon=1, measurement_plans=plans)(lambda e, layout, p: None)
