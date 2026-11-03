"""Profiler aggregation, real query neutrality and frozen input boundaries."""

from argparse import Namespace
from pathlib import Path

import pytest

from simulatte import Environment
from simulatte.experimental.native import NativeProduction
from simulatte.stopping import Horizon
from prototypes.native_projection_profile import analysis, measurement as m, probe, run


def test_function_categories_are_explicit_and_nonoverlapping():
    assert m.category("/x/simulatte/_wire.py", "freeze") == "wire_preparation"
    assert m.category("/x/simulatte/_wire.py", "_packb") == "messagepack"
    assert m.category("/x/simulatte/_wire.py", "__iter__") == "detachment_access"
    assert (
        m.category("x", "<built-in method simulatte_native._nd_scalar.retention_state_projection>") == "native_atomic"
    )
    assert m.category("/x/fixed_case.py", "state") == "callback"


def test_profile_self_categories_partition_accounted_time(tmp_path):
    value = dict(x=[1, 2, 3])
    result = m.profile(lambda: m.reference.pack(value), 4, tmp_path / "sample.prof")
    assert sum(result["self_categories"].values()) == pytest.approx(result["accounted_seconds"])
    assert result["calls"] == 4 and result["functions"] and (tmp_path / "sample.prof").exists()
    assert m.timed(lambda: None, 8)["calls"] == 8


def test_latency_aggregates_per_call_rates_before_taking_median():
    query = dict(
        batches=dict(
            stage=[
                dict(calls=2, wall_ns=2000, cpu_ns=1000),
                dict(calls=8, wall_ns=32000, cpu_ns=16000),
                dict(calls=1, wall_ns=3000, cpu_ns=1500),
            ]
        )
    )
    assert analysis.latency(query, "stage") == 3.0
    assert analysis.latency(query, "stage", "cpu") == 1.5


@pytest.mark.parametrize("retention", ["full", "bounded"])
def test_real_measurement_is_state_and_callback_neutral(tmp_path, retention):
    pytest.importorskip("simulatte_native")
    state = dict(seed=1, future=[dict(id=0, due=5.0)])
    with Environment() as env:
        env._execution_policy = Horizon(10)
        backend = NativeProduction(1, retention=retention, input_state=lambda: state)
        env.attach_execution_backend(backend)
        env.activate()
        before = backend.state_digest()
        result = m.measure(
            backend,
            lambda: state,
            m.reference.Actions().state(),
            tmp_path / "measurement",
            seed=1,
            blocks=1,
            target_ns=100_000,
        )
        assert backend.state_digest() == before
        assert result["context"]["state"] == m.capture_state(backend, lambda: state)
        assert state == dict(seed=1, future=[dict(id=0, due=5.0)])
        assert len(result["batches"]) == 17 and all(len(v) == 1 for v in result["batches"].values())
        assert set(result["profiles"]) == {"managed_hook", "composition_only", "pipeline"}
        assert result["gc_enabled"]


def test_impure_source_does_not_become_a_profile_input():
    pytest.importorskip("simulatte_native")
    state = dict(value=0)

    def impure():
        state["value"] += 1
        return state

    with Environment() as env:
        env._execution_policy = Horizon(10)
        backend = NativeProduction(1, input_state=impure)
        env.attach_execution_backend(backend)
        env.activate()
        with pytest.raises((ValueError, AssertionError)):
            m.prepare_operations(backend, impure, m.reference.Actions().state())


@pytest.mark.parametrize("fixture", ["fixed", "coupled"])
def test_profile_queries_preserve_complete_model_artifacts(tmp_path, fixture):
    pytest.importorskip("simulatte_native")
    args = Namespace(
        kind="live",
        fixture=fixture,
        retention="bounded",
        seed=100,
        policy="spt",
        jobs=128,
        padding=0,
        blocks=1,
        target_ns=100_000,
    )
    a = probe.simulate(tmp_path / "baseline", args, profiled=False)
    b = probe.simulate(tmp_path / "profiled", args, profiled=True)
    assert a["fingerprint"] == b["fingerprint"] and a["artifacts"] == b["artifacts"]


def test_cell_matrix_and_frozen_source_copies():
    cells = run.cells()
    assert len(cells) == 54 and sum(v["kind"] == "live" for v in cells) == 24
    root = Path(__file__).resolve().parents[2]
    for name in ("fixed_case.py", "coupled_case.py", "plant.py", "projection.py", "reference_projection.py"):
        assert (root / "native_projection_profile" / name).read_bytes() == (
            root / "native_provider_projection" / name
        ).read_bytes()
