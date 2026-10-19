"""Declared observations must retain timing and isolate predicates from live entities."""

from dataclasses import dataclass
import importlib
import importlib.util
import json
from types import SimpleNamespace

import pytest

from simulatte import Environment, model
from simulatte.model import get_model_spec


def api():
    assert importlib.util.find_spec("simulatte.analysis") is not None, "measurement API is missing"
    return importlib.import_module("simulatte.analysis")


def test_membership_uses_declared_observation_time():
    a = api()
    fields = (a.Field("family", "string"), a.Field("skills", {"list": "string"}))
    job = SimpleNamespace(family="A", skills=["lift"])
    seen = []

    def predicate(row):
        seen.append(row)
        assert not hasattr(row, "env")
        with pytest.raises(TypeError):
            row["family"] = "changed"
        with pytest.raises((AttributeError, TypeError)):
            row["skills"].append("changed")
        return row["family"] in ("A", "B") and "lift" in row["skills"]

    collectors = []
    for timing in ("arrival", "completion"):
        schema = a.DatasetSchema(timing, fields, observation=timing)
        populations = (
            a.Population("capable", predicate, timing, fields=("family", "skills")),
            a.Population("all", lambda row: True, timing),
        )
        collector = a.ObservationCollector(
            schema, fields={"family": lambda obj: obj.family, "skills": lambda obj: obj.skills}, populations=populations
        )
        collector.record(job, observed_at=timing, values={})
        collectors.append(collector)
        job.family = "B"
    job.family = "C"
    job.skills.append("tow")
    arrival_row, completion_row = [c.rows[0] for c in collectors]
    assert arrival_row["family"] == "A"
    assert completion_row["family"] == "B"
    assert arrival_row["skills"] == ("lift",)
    assert collectors[0].memberships == (("capable", "all"),)
    assert len(seen) == 2


@pytest.mark.parametrize("dtype", ["object", {"list": "object"}, {"record": {"x": "object"}}, {}])
def test_invalid_field_types(dtype):
    with pytest.raises(ValueError, match="type"):
        api().Field("x", dtype)


def test_duplicate_fields_and_wrong_values():
    a = api()
    with pytest.raises(ValueError, match="Duplicate"):
        a.DatasetSchema("jobs", (a.Field("x", "int64"), a.Field("x", "string")))
    c = a.ObservationCollector(a.DatasetSchema("jobs", (a.Field("x", "int64"),)), fields={})
    c.record(None, observed_at="observation", values={"x": 2**63})
    assert not c.rows
    assert "int64" in c.failure
    with pytest.raises(ValueError, match="undeclared"):
        a.ObservationCollector(c.schema, fields={"extra": lambda obj: 1})


def test_plain_python_and_disabled_plan():
    a = api()
    schema = a.DatasetSchema("jobs", (a.Field("x", "int64"),))
    with Environment(seed=2) as env:
        env.configure_measurements(a.MeasurementPlan("numbers", (schema,)))
        c = a.ObservationCollector(schema, fields={}).attach(env)
        c.record(None, observed_at="observation", values={"x": 2**60})
        assert c.rows[0]["x"] == 2**60
        assert env.fingerprint().kpis == {}
        env.run(until=1)
        with pytest.raises(RuntimeError, match="activation"):
            env.configure_measurements(a.MeasurementPlan("off", ()))
    with Environment(seed=2) as env:
        env.configure_measurements(a.MeasurementPlan("off", ()))
        c = a.ObservationCollector(schema, fields={"x": lambda obj: pytest.fail("disabled")}).attach(env)
        c.record(None, observed_at="observation", values={})
        assert c.rows == () and c._rows is None
        assert c not in env.collectors
        assert c._subscription is None


def test_callback_failure_does_not_interrupt_baseline():
    a = api()

    def fail(row):
        raise RuntimeError("bad predicate")

    schema = a.DatasetSchema("jobs", (a.Field("x", "int64"),))
    with Environment(seed=42) as env:
        c = a.ObservationCollector(schema, fields={}, populations=(a.Population("bad", fail),)).attach(env)

        def work():
            yield env.timeout(1)
            c.record(None, observed_at="observation", values={"x": 4})
            yield env.timeout(1)

        env.process(work())
        env.run()
        assert env.now == 2
        assert c.failure == "jobs: RuntimeError: bad predicate"
        assert c.rows == ()


def test_declarations_hash_and_model_inspection(tmp_path):
    a = api()
    schema = a.DatasetSchema("jobs", (a.Field("x", "float64", unit="minute"),))
    plan = a.MeasurementPlan("numbers", (schema,), parameters={"threshold": 2})
    copy = json.loads(json.dumps(plan.to_dict()))
    assert copy["datasets"][0]["fields"][0]["unit"] == "minute"
    assert a.definition_hash(plan) != a.definition_hash(
        a.MeasurementPlan("numbers", (schema,), parameters={"threshold": 3})
    )

    @dataclass(frozen=True)
    class Params:
        amount: int = 1

    def build(env, layout, params):
        return params.amount

    assert model(params=Params, horizon=1, measurement_plans=(plan,))(build) is build
    assert get_model_spec(build).measurement_plans == (plan,)
    path = tmp_path / "model.py"
    path.write_text("""from dataclasses import dataclass
from simulatte import model
from simulatte.analysis import MeasurementPlan, DatasetSchema, Field
@dataclass(frozen=True)
class Params: pass
plan = MeasurementPlan("n", (DatasetSchema("j", (Field("x", "int64"),)),))
@model(params=Params, horizon=1, measurement_plans=(plan,))
def build(env, layout, params): pass
""")
    from simulatte.execution.bootstrap import inspect_model

    metadata = inspect_model("model.py", (tmp_path,))
    assert metadata["models"][0]["measurement_plans"][0]["name"] == "n"
    json.dumps(metadata)


def test_analysis_metadata_and_stricter_requirements():
    a = api()

    @dataclass(frozen=True)
    class Params:
        threshold: int = 2

    req = a.DatasetRequirement("jobs", ("lateness",), min_replications=3)

    def analyze(context, parameters):
        pass

    assert a.analysis(parameters=Params, requires=(req,))(analyze) is analyze
    definition = a.get_analysis_definition(
        analyze, id="lateness", entrypoint="analysis.py:analyze", source_hash="a" * 64
    )
    metadata = definition.to_dict()
    assert metadata["requirements"][0]["min_replications"] == 3
    assert metadata["parameters"]["fields"][0]["default"] == 2
    combined = a.combine_requirements((req,), (a.DatasetRequirement("jobs", ("family",), 2, True),))
    assert combined[0].fields == ("family", "lateness")
    assert combined[0].min_replications == 3 and combined[0].require_complete


def test_nested_typed_copy_and_roundtrip():
    a = api()
    schema = a.DatasetSchema(
        "typed",
        (
            a.Field("record", {"record": {"flag": "bool", "items": {"list": "float64"}}}),
            a.Field("missing", "string", nullable=True),
            a.Field("nothing", "null"),
        ),
    )
    source = {"record": {"flag": True, "items": [1.5]}, "missing": None, "nothing": None}
    row = schema.row(source)
    source["record"]["items"].append(9.0)
    assert row["record"]["items"] == (1.5,)
    assert list(row) == ["record", "missing", "nothing"] and len(row) == 3
    assert a.DatasetSchema.from_dict(schema.to_dict()) == schema
    for values in ({}, {**source, "extra": 2}, {**source, "missing": 3}, {**source, "nothing": False}):
        with pytest.raises(ValueError):
            schema.row(values)
    for bad in (object(), {2: "bad"}, 2**63, -(2**63) - 1):
        with pytest.raises(ValueError):
            a.Observation({"value": bad})
    nested = None
    for _ in range(34):
        nested = [nested]
    with pytest.raises(ValueError, match="nesting"):
        a.Observation({"value": nested})


def test_invalid_declarations():
    a = api()
    schema = a.DatasetSchema("jobs", (a.Field("x", "int64"),))
    for make in (
        lambda: a.Field("../x", "int64"),
        lambda: a.Field("x", "string", nullable=1),
        lambda: a.Field("x", "string", unit=""),
        lambda: a.DatasetSchema("x", ()),
        lambda: a.DatasetSchema("x", [a.Field("x", "int64")]),
        lambda: a.DatasetSchema("x", (a.Field("x", "int64"),), observation=""),
        lambda: a.Population("x", None),
        lambda: a.Population("x", bool, fields=("x", "x")),
        lambda: a.MeasurementPlan("x", [schema]),
        lambda: a.MeasurementPlan("x", (schema, schema)),
        lambda: a.MeasurementPlan("x", (schema,), parameters={"x": float("nan")}),
        lambda: a.DatasetRequirement("x", ("x", "x")),
        lambda: a.DatasetRequirement("x", (), -1),
        lambda: a.DatasetRequirement("x", (), require_complete=1),
        lambda: a.ObservationCollector(schema, fields={"x": None}),
        lambda: a.ObservationCollector(schema, fields={}, populations=(a.Population("x", bool, "arrival"),)),
        lambda: a.ObservationCollector(schema, fields={}, populations=(a.Population("x", bool, fields=("other",)),)),
        lambda: a.ObservationCollector(schema, fields={}, populations=(a.Population("x", bool),) * 2),
    ):
        with pytest.raises(ValueError):
            make()
    from simulatte.model import ParameterSchema

    decorated = a.analysis(parameters=ParameterSchema(()))(lambda ctx, params: None)
    with pytest.raises(ValueError):
        a.analysis(parameters=ParameterSchema(()))(decorated)
    with pytest.raises(ValueError):
        a.get_analysis_definition(lambda: None, id="x", entrypoint="x.py:f", source_hash="a" * 64)


def test_observation_failures_timing_and_attachments():
    a = api()
    schema = a.DatasetSchema("jobs", (a.Field("x", "int64"),))
    c = a.ObservationCollector(schema, fields={"x": lambda obj: 1})
    c.record(None, observed_at="arrival", values={})
    assert c.rows == ()
    c.record(None, observed_at="observation", values={"x": 2})
    assert "Duplicate" in c.failure
    c.record(None, observed_at="observation", values={})
    assert not c.rows
    c = a.ObservationCollector(schema, fields={}, populations=(a.Population("not_bool", lambda row: 1),))
    c.record(None, observed_at="observation", values={"x": 1})
    assert "must return bool" in c.failure
    with Environment(seed=1) as env:
        c.attach(env)
        with pytest.raises(RuntimeError, match="already attached"):
            c.attach(env)
        with pytest.raises(TypeError):
            env.configure_measurements(None)
