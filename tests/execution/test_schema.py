"""Execution identity includes inputs but excludes attempt/host timing."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import importlib
import importlib.util

import pytest


def api():
    assert importlib.util.find_spec("simulatte.execution") is not None, "execution package missing"
    return importlib.import_module("simulatte.execution.schema")


def request(m, **changes):
    return m.ExecutionRequest(
        **(
            {
                "model": "plant.py:build",
                "source_hash": "a" * 64,
                "input_hash": "b" * 64,
                "layout_hash": "c" * 64,
                "layout_revision": 1,
                "parameters": {"x": 1.0},
                "seed": 0,
                "stopping": {"type": "horizon", "horizon": 10.0, "warmup": 0.0},
                "level": "full",
                "kpis": ("shop/leadtime",),
                "runtime": {"implementation": "CPython", "version": "3.14.1"},
            }
            | changes
        )
    )


def test_request_roundtrip_immutable_and_frozen_mappings():
    m = api()
    data = {"x": 1.0}
    r = request(m, parameters=data)
    data["x"] = 2.0
    assert r.to_dict()["parameters"] == {"x": 1.0}
    assert r.to_dict()["seed"] == "0"
    assert m.ExecutionRequest.from_dict(r.to_dict()) == r
    with pytest.raises(FrozenInstanceError):
        r.seed = 4
    with pytest.raises(TypeError):
        r.parameters["x"] = 3
    assert len(m.request_id(r)) == 64


def test_request_identity_covers_every_execution_input():
    m = api()
    r = request(m)
    assert m.request_id(r) == m.request_id(m.ExecutionRequest.from_dict(dict(reversed(list(r.to_dict().items())))))
    for key, value in {
        "model": "other.py:build",
        "source_hash": "d" * 64,
        "input_hash": "e" * 64,
        "layout_hash": "f" * 64,
        "layout_revision": 2,
        "parameters": {"x": 2.0},
        "seed": 1,
        "stopping": {"type": "horizon", "horizon": 20.0, "warmup": 0.0},
        "level": "kpi",
        "kpis": ("other/key",),
        "runtime": {"version": "3.13"},
    }.items():
        assert m.request_id(replace(r, **{key: value})) != m.request_id(r), key
    with pytest.raises(ValueError):
        m.ExecutionRequest.from_dict(r.to_dict() | {"host": "eva"})


@pytest.mark.parametrize(
    "changes",
    [
        {"seed": True},
        {"seed": -1},
        {"seed": 2**63},
        {"source_hash": "x"},
        {"model": "../x.py:build"},
        {"layout_revision": True},
        {"level": "silent"},
        {"kpis": ("x", "x")},
        {"parameters": {"x": float("nan")}},
        {"parameters": {"x": 2**53}},
        {"stopping": {"type": "event"}},
    ],
)
def test_request_rejects_invalid_identity(changes):
    m = api()
    with pytest.raises((ValueError, OverflowError)):
        request(m, **changes)


def test_stop_policy_metadata_values():
    import simulatte

    assert hasattr(simulatte, "Horizon"), "typed stopping policies missing"
    assert simulatte.Horizon(5).to_dict() == {"type": "horizon", "horizon": 5.0, "warmup": 0.0}
    assert simulatte.FinitePopulation(arrivals=0, safety_limit=10).to_dict() == {
        "type": "finite_population",
        "arrivals": 0,
        "cutoff": None,
        "safety_limit": 10.0,
    }
    for make in [
        lambda: simulatte.Horizon(True),
        lambda: simulatte.Horizon(1, 1),
        lambda: simulatte.FinitePopulation(safety_limit=10),
        lambda: simulatte.FinitePopulation(arrivals=1, cutoff=3, safety_limit=10),
        lambda: simulatte.FinitePopulation(cutoff=10, safety_limit=10),
    ]:
        with pytest.raises(ValueError):
            make()


def test_attempt_revision_and_resolved_manifest_validation():
    m = api()
    for make in [
        lambda: m.Revision(True, "a" * 64),
        lambda: m.Revision(1, "bad"),
        lambda: m.AttemptKey("a" * 64, "wrong", 1),
        lambda: m.AttemptKey("a" * 64, "b" * 32, 0),
        lambda: request(m, runtime=[]),
        lambda: m.ResolvedManifest(runtime={}, dependencies={}, imports={}, layout={}, capabilities={}, warnings=(1,)),
    ]:
        with pytest.raises(ValueError):
            make()
    key = m.AttemptKey("a" * 64, "b" * 32, 1)
    assert key.generation == 1
    values = {"lib": "1.0"}
    resolved = m.ResolvedManifest(runtime={}, dependencies=values, imports={}, layout={}, capabilities={})
    values["lib"] = "changed"
    assert resolved.to_dict()["dependencies"] == {"lib": "1.0"}
    assert resolved.to_dict()["schema"] == 1
    r = request(m)
    for seed in ["01", "-1", "1.0", True, "9" * 20]:
        with pytest.raises(ValueError):
            m.ExecutionRequest.from_dict(r.to_dict() | {"seed": seed})


def test_stopping_rejects_nonfinite_and_negative_cutoffs():
    from simulatte import Horizon, FinitePopulation

    for make in [
        lambda: Horizon(10**1000),
        lambda: Horizon(float("inf")),
        lambda: FinitePopulation(arrivals=-1, safety_limit=3),
        lambda: FinitePopulation(cutoff=-1, safety_limit=3),
        lambda: FinitePopulation(arrivals=1, safety_limit=0),
    ]:
        with pytest.raises(ValueError):
            make()
