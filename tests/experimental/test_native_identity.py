"""Public composition preserves frozen canonical bytes and model behavior."""

import copy
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from simulatte.experimental.native_identity import compose_native_identity
from prototypes.native_prepared_identity import projection as oracle
from prototypes.native_prepared_identity.tests.test_models import simulate
from prototypes.native_prepared_identity.tests.test_prepared import view


@pytest.mark.parametrize(
    "inputs",
    [
        None,
        {},
        [0.0, -0.0, float("inf"), float("-inf"), float("nan")],
        {"__proto__": {"~π": [True, None]}, "😀": 2**53 - 1},
    ],
)
def test_exact_frozen_bytes_and_sizes(inputs):
    value, actions = view(inputs), oracle.Actions().state()
    result = compose_native_identity(value, actions=actions)
    expected, sizes = oracle.hook_capture(SimpleNamespace(state_projection=lambda: value), actions)
    assert result.canonical_bytes == oracle.reference.pack(expected)
    assert result.canonical_bytes == oracle.canonical_pack(oracle.prepare(value, actions))
    assert result.size_bytes == sizes["view_bytes"] == len(result.canonical_bytes)
    assert result.native_bytes == sizes["raw_bytes"]


def test_inputs_outputs_and_snapshots_are_independent():
    value, actions = view(dict(future=[dict(id=1)])), dict(rows=[dict(id=1)])
    first = compose_native_identity(value, actions=actions)
    original = first.canonical_bytes
    value["authority_preimage"]["inputs"]["future"].clear()
    actions["rows"].clear()
    detached = first.to_dict()
    detached["authority"]["digest"] = "changed"
    detached["evidence"]["trajectory"]["actions"]["rows"].clear()
    assert first.canonical_bytes == original == oracle.reference.pack(first.to_dict())
    assert first.to_dict()["evidence"]["reproduction"]["authority"]["digest"] != "changed"
    assert first.to_dict()["evidence"]["trajectory"]["actions"]["rows"] == [dict(id=1)]
    assert compose_native_identity(value, actions=actions).canonical_bytes != original
    with pytest.raises(FrozenInstanceError):
        first.native_bytes = 0


@pytest.mark.parametrize("field", ["actions", "reporting", "future"])
def test_identity_components_preserve_future_and_reporting_distinctions(field):
    value, actions = view(dict(seed=1)), oracle.Actions().state()
    first = compose_native_identity(value, actions=actions).to_dict()
    if field == "actions":
        actions["rows"] += 1
    elif field == "reporting":
        value["reporting"]["configuration"]["warmup"] += 1
    else:
        value["authority_preimage"]["inputs"]["seed"] += 1
        value["authority"]["digest"] = oracle.digest(value["authority_preimage"])
    second = compose_native_identity(value, actions=actions).to_dict()
    oracle.validate(first)
    oracle.validate(second)
    assert (first["trajectory_control"] == second["trajectory_control"]) is (field != "actions")
    assert (first["reporting"] == second["reporting"]) is (field != "reporting")
    assert first["reproduction_state"] != second["reproduction_state"]


@pytest.mark.parametrize("mutation", ["schema", "field", "reporting", "catalog", "negative", "bool", "float", "root"])
def test_invalid_projection_shapes(mutation):
    value = view()
    if mutation == "schema":
        value["schema"] = "native-stream-projection-v1"
    elif mutation == "field":
        del value["authority_preimage"]
    elif mutation == "reporting":
        value["reporting"] = []
    elif mutation == "catalog":
        del value["reporting"]["catalog"]
    elif mutation == "root":
        value = []
    else:
        value["native_bytes"] = {"negative": -1, "bool": True, "float": 1.0}[mutation]
    with pytest.raises(ValueError):
        compose_native_identity(value, actions={})


@pytest.mark.parametrize("invalid", [object(), b"bytes", {1: "nonstring key"}])
def test_non_wire_action_values(invalid):
    with pytest.raises(TypeError):
        compose_native_identity(view(), actions=invalid)


def test_no_user_mapping_methods_and_safe_integer_limit():
    class Hostile(dict):
        def __getitem__(self, key):
            raise AssertionError("called user code")

        def items(self):
            raise AssertionError("called user code")

    assert compose_native_identity(Hostile(view()), actions=Hostile()).size_bytes > 0
    with pytest.raises(OverflowError):
        compose_native_identity(view(), actions=dict(id=2**53))


@pytest.mark.parametrize("fixture", ["fixed", "coupled"])
@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_helper_preserves_all_artifacts_at_every_poll(tmp_path, monkeypatch, fixture, retention):
    pytest.importorskip("simulatte_native")
    monkeypatch.setenv("SIMULATTE_IDENTITIES_QUERY_EVERY", "1")
    monkeypatch.setenv("SIMULATTE_PROJECTION_MODE", "hook")
    frozen = oracle.hook_capture

    def helper(backend, actions):
        result = compose_native_identity(backend.state_projection(), actions=actions)
        return result.to_dict(), dict(raw_bytes=result.native_bytes, view_bytes=result.size_bytes)

    with monkeypatch.context() as context:
        context.setattr(oracle, "hook_capture", helper)
        actual = simulate(tmp_path / "helper", fixture, retention)
    assert oracle.hook_capture is frozen
    for mode in ("hook", "unprepared", "reference"):
        monkeypatch.setenv("SIMULATTE_PROJECTION_MODE", mode)
        assert simulate(tmp_path / mode, fixture, retention) == actual


def test_composer_is_not_an_authority_verifier():
    value = copy.deepcopy(view(dict(seed=1)))
    value["authority"]["digest"] = "0" * 64
    composed = compose_native_identity(value, actions=oracle.Actions().state()).to_dict()
    assert composed["evidence"]["authority_preimage"] == value["authority_preimage"]
    with pytest.raises(ValueError):
        oracle.validate(composed)
