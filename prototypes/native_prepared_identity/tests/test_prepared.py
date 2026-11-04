"""Prepared bytes, detached output and fresh-authority behavior against both oracles."""

import copy
import random
from pathlib import Path
from types import SimpleNamespace

import pytest

from simulatte import Environment
from simulatte.experimental.native import NativeProduction
from simulatte.stopping import Horizon
from prototypes.native_prepared_identity import projection as p


def view(inputs=None):
    result = dict(
        schema="native-production-projection-v1",
        scope=dict(backend="native-production", scope="shopfloor", time_unit=None),
        trajectory=dict(codec="NSTA0001", physical_sha256="1" * 64),
        reporting=dict(
            configuration=dict(type="horizon", horizon=10.0, warmup=0.0),
            catalog=[],
            snapshot=dict(window=[0.0, 0.0, False], scalars={}),
        ),
        authority=dict(schema="native-production-v1", digest="2" * 64),
        authority_preimage=dict(schema="native-production-v1", native="1" * 64, inputs=inputs),
        native_bytes=112,
    )
    result["authority"]["digest"] = p.digest(result["authority_preimage"])
    return result


def compare(value):
    backend = SimpleNamespace(state_projection=lambda: value)
    actions = p.Actions().state()
    baseline = p.unprepared.hook_capture(backend, actions)
    candidate = p.hook_capture(backend, actions)
    packed = p.canonical_pack(p.prepare(value, actions))
    assert packed == p.reference.pack(candidate[0]) == p.reference.pack(baseline[0])
    assert candidate[1] == baseline[1] and candidate[1]["view_bytes"] == len(packed)
    return candidate


@pytest.mark.parametrize(
    "inputs",
    [
        None,
        {},
        [0.0, -0.0, float("inf"), float("-inf"), float("nan")],
        {"__proto__": {"~name": "π", "constructor": [1, True, None]}, "😀": -(2**53) + 1},
    ],
)
def test_wire_edge_cases_have_identical_canonical_bytes(inputs):
    compare(view(inputs))


def test_random_nested_wire_inputs_and_key_orders():
    rng = random.Random(20261104)
    keys = ["x", "a", "~escaped", "__proto__", "constructor", "π", "😀"]
    for _ in range(40):
        rng.shuffle(keys)
        value = {
            k: [rng.randrange(-1000, 1000), rng.random(), dict(tag=k, enabled=bool(rng.randrange(2)))] for k in keys
        }
        compare(view(value))


def test_prepared_graph_is_detached_and_reuses_immutable_subtrees():
    value, actions = view(dict(pending=[dict(id=0)])), p.Actions().state()
    prepared = p.prepare(value, actions)
    packed = p.canonical_pack(prepared)
    assert prepared["authority"] is prepared["evidence"]["reproduction"]["authority"]
    assert prepared["evidence"]["trajectory"]["scope"] is prepared["evidence"]["report"]["scope"]
    value["authority_preimage"]["inputs"]["pending"].clear()
    actions["rows"] = 123
    assert p.canonical_pack(prepared) == packed
    plain = p.reference.plain(prepared)
    plain["authority"]["digest"] = "bad"
    assert plain["evidence"]["reproduction"]["authority"]["digest"] == prepared["authority"]["digest"]
    assert p.canonical_pack(prepared) == packed


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_real_provider_complete_authority_and_callback_freshness(retention):
    pytest.importorskip("simulatte_native")
    state, calls = dict(seed=1, future=[dict(id=0, due=4.0)]), []

    def inputs():
        calls.append(None)
        return state

    with Environment() as env:
        env._execution_policy = Horizon(10)
        backend = NativeProduction(1, retention=retention, input_state=inputs)
        env.attach_execution_backend(backend, scope="shopfloor")
        env.activate()
        actions = p.Actions().state()
        calls.clear()
        first = p.hook_capture(backend, actions)
        assert len(calls) == 2
        p.validate(first[0])
        original = p.reference.capture(backend, inputs, actions)
        assert first == original == p.unprepared.hook_capture(backend, actions)
        state["seed"] += 1
        second = p.hook_capture(backend, actions)
        assert second[0]["trajectory_control"] == first[0]["trajectory_control"]
        assert second[0]["authority"] != first[0]["authority"]
        assert second[0]["reproduction_state"] != first[0]["reproduction_state"]
        with pytest.raises(ValueError, match="Complete reproduction"):
            p.require_reproduction(first[0], second[0])
        state["seed"] -= 1
        assert p.hook_capture(backend, actions) == first
    with pytest.raises(RuntimeError):
        p.hook_capture(backend, actions)


def test_each_capture_requests_one_fresh_public_view():
    values = [view(dict(seed=1)), view(dict(seed=2))]
    backend = SimpleNamespace(state_projection=lambda: values.pop(0))
    first = p.hook_capture(backend, p.Actions().state())
    second = p.hook_capture(backend, p.Actions().state())
    assert not values
    assert first[0]["evidence"]["authority_preimage"]["inputs"] != second[0]["evidence"]["authority_preimage"]["inputs"]


def test_bad_schema_and_non_wire_payload_rejected():
    value = view()
    value["schema"] = "unknown-v9"
    with pytest.raises(ValueError):
        p.prepare(value, p.Actions().state())
    with pytest.raises(TypeError):
        p.prepare(view(object()), p.Actions().state())


def test_dropped_authority_is_still_rejected_by_unchanged_validator():
    result = compare(view())[0]
    p.validate(result)
    bad = copy.deepcopy(result)
    bad["authority"]["digest"] = "0" * 64
    with pytest.raises(ValueError):
        p.validate(bad)


def test_frozen_sources_are_byte_identical():
    root = Path(__file__).resolve().parents[2]
    for new, old in (
        ("fixed_case.py", "fixed_case.py"),
        ("coupled_case.py", "coupled_case.py"),
        ("plant.py", "plant.py"),
        ("unprepared_projection.py", "projection.py"),
        ("reference_projection.py", "reference_projection.py"),
    ):
        assert (root / "native_prepared_identity" / new).read_bytes() == (
            root / "native_provider_projection" / old
        ).read_bytes()
